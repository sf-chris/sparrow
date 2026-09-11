"""Persisted transfer intent; request controls report confirmed versus pending effects."""

import time
from .models import JobStatus, SessionStatus, Event, CaseState
from .node_executor import NodeError
from .node_tools import components
from ..models import DownloadStatus


async def apply_control(service, job, download):
    action = download.metadata.get("desired_control")
    if not action:
        return True
    try:
        if download.metadata.get("node_id"):
            nodes, _ = components(service.toolbox)
            node = nodes.info(download.metadata["node_id"])
            if not node or not node["online"]:
                return False
            kind = {
                "stop": "download_stop",
                "start": "download_start",
                "remove": "download_remove",
            }[action]
            await nodes.execute(
                download.metadata["node_id"],
                kind,
                {"hash": download.torrent_hash},
                job=job,
                timeout=15,
            )
        else:
            manager = await service._connected_manager()
            if not manager:
                return False
            if action == "stop":
                result = await manager.stop_torrent(download.torrent_hash)
            elif action == "start":
                result = await manager.start_torrent(download.torrent_hash)
            else:
                result = await manager.delete_torrent(
                    download.torrent_hash, delete_files=False
                )
            if result is False:
                return False
    except Exception:
        return False
    current = service.store.get_job(job.id)
    if not current or current.revision != job.revision:
        return False
    metadata = {
        **download.metadata,
        "desired_control": "",
        "control_confirmed_at": time.time(),
    }
    status = {
        "stop": DownloadStatus.PAUSED,
        "start": DownloadStatus.DOWNLOADING,
        "remove": DownloadStatus.ERROR,
    }[action]
    await service.storage.update_download(
        download.id,
        status=status,
        metadata=metadata,
        error_message="Cancelled; files retained." if action == "remove" else "",
    )
    if not any(
        d.metadata.get("desired_control")
        for d in service.storage.get_all_downloads()
        if d.metadata.get("job_id") == job.id
    ):
        job.state_line = {
            JobStatus.PAUSED: "Paused. Downloads stopped; files retained.",
            JobStatus.ACTIVE: "Resumed.",
            JobStatus.ABANDONED: "Cancelled. Transfers removed; files retained.",
        }.get(job.status, job.state_line)
        service.store.save_job(job)
        await service.broadcast({"type": "job_update", "data": job.to_dict()})
    return True


async def control_job(service, job_id, action):
    job = service.store.get_job(job_id)
    if not job:
        return None
    job.revision += 1
    job.status = {
        "pause": JobStatus.PAUSED,
        "resume": JobStatus.ACTIVE,
        "cancel": JobStatus.ABANDONED,
    }[action]
    job.state_line = {
        "pause": "Paused. Confirming stopped transfers…",
        "resume": "Resuming. Checking storage…",
        "cancel": "Cancelled. Confirming transfer removal; files retained.",
    }[action]
    job.closed_at = time.time() if action == "cancel" else None
    job.next_wake_at = 0
    service.store.save_job(job)
    for session in service.store.get_sessions(job_id=job_id):
        session.wake_at = 0
        if action == "cancel":
            session.status = SessionStatus.CLOSED
            session.outcome = CaseState.CANCELLED
            session.close_reason = "Cancelled by the requester."
            session.closed_at = time.time()
        elif action == "resume" and session.agent.value == "fetch":
            session.job_revision = job.revision
            session.status = SessionStatus.HIBERNATING
            session.outcome = CaseState.WAITING
            session.closed_at = None
            session.close_reason = ""
        elif (
            action == "resume"
            and session.agent.value == "media"
            and session.status != SessionStatus.CLOSED
        ):
            session.job_revision = job.revision
            session.status = SessionStatus.HIBERNATING
            session.outcome = CaseState.WAITING
        service.store.save_session(session)
    await service.broadcast({"type": "job_update", "data": job.to_dict()})
    pending = False
    async with service.toolbox.transfer_lock:
        # Pause/cancel serialize after in-flight adds; the revision already revoked new work.
        if service.store.get_job(job.id).revision != job.revision:
            return service.store.get_job(job.id)
        for download in service.storage.get_all_downloads():
            if (
                download.metadata.get("job_id") != job.id
                or download.status == DownloadStatus.ORGANIZED
            ):
                continue
            if (
                action == "resume"
                and download.status != DownloadStatus.PAUSED
                and not download.metadata.get("desired_control")
            ):
                continue
            if action != "resume" and download.status == DownloadStatus.ERROR:
                continue
            desired = {"pause": "stop", "resume": "start", "cancel": "remove"}[action]
            metadata = {
                **download.metadata,
                "desired_control": desired,
                "job_revision": job.revision,
            }
            await service.storage.update_download(download.id, metadata=metadata)
            refreshed = service.storage.get_download(download.id)
            confirmed = await apply_control(service, job, refreshed)
            pending = pending or not confirmed
            if not confirmed:
                await service.storage.update_download(
                    download.id,
                    error_message="Waiting for storage to confirm " + desired + ".",
                )
            await service.broadcast(
                {
                    "type": "download_update",
                    "data": service.storage.get_download(download.id).to_dict(),
                }
            )
    current = service.store.get_job(job.id)
    if current.revision != job.revision:
        return current
    if pending:
        job.state_line = {
            "pause": "Paused. Waiting for storage to confirm stopped downloads.",
            "resume": "Resumed. Waiting for storage to restart downloads.",
            "cancel": "Cancelled. Transfer removal will finish when storage reconnects; files are retained.",
        }[action]
    else:
        job.state_line = {
            "pause": "Paused. Downloads stopped; files retained.",
            "resume": "Resumed.",
            "cancel": "Cancelled. Transfers removed; library and staging files retained.",
        }[action]
    service.store.save_job(job)
    await service.broadcast({"type": "job_update", "data": job.to_dict()})
    if action == "resume":
        await service.emit(
            Event(
                kind="resume",
                job_id=job.id,
                payload={
                    "description": "The requester resumed this work. Reconcile transfers and unfinished media."
                },
            )
        )
    return job
