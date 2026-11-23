"""Persisted transfer intent; request controls report confirmed versus pending effects."""

import logging
import time
from .models import JobStatus, SessionStatus, Event, CaseState
from .node_executor import NodeError
from .node_tools import components
from ..models import DownloadStatus

logger = logging.getLogger(__name__)

# An ended request's leftover download waits this long so "Try again" or a
# repeat request can pick it back up instead of fetching it again.
RELEASE_AFTER = 24 * 3600
LIVE = (JobStatus.ACTIVE, JobStatus.PAUSED)
# Finished copies the download app may still be sharing under the seeding policy.
SHARING = (
    DownloadStatus.COMPLETED,
    DownloadStatus.SEEDING,
    DownloadStatus.ORGANIZING,
    DownloadStatus.ORGANIZED,
)


def releasable(service, download, now):
    """The request that owns this agent download ended a day ago and the copy
    is not being shared: nothing will use its staging folder again."""
    md = download.metadata
    if not md.get("agent_managed") or md.get("released_at") or md.get("desired_control"):
        return None
    if not md.get("node_id"):
        return None  # the legacy downloader path keeps its own folders
    job = service.store.get_job(md.get("job_id", ""))
    if not job or job.status in LIVE or now - (job.closed_at or now) < RELEASE_AFTER:
        return None
    if download.status in SHARING and not md.get("seeding_stopped"):
        return None
    return job


async def release_finished(service, now=None):
    """Remove an ended request's leftover transfers from the download app and
    delete their staging folders: unfinished parts, unplaced files and the
    originals of copies already verified into the library. Library files are
    never touched. Retried each poll until storage confirms."""
    nodes, _ = components(service.toolbox)
    now = now or time.time()
    for candidate in service.storage.get_all_downloads():
        if not releasable(service, candidate, now):
            continue
        async with service.toolbox.transfer_lock:
            # A new request may have taken the copy over while we waited.
            download = service.storage.get_download(candidate.id)
            job = download and releasable(service, download, now)
            if not job:
                continue
            node_id = download.metadata["node_id"]
            node = nodes.info(node_id)
            if not node or not node["online"]:
                continue
            attempt = int(download.metadata.get("attempt") or 0)
            try:
                await nodes.execute(
                    node_id,
                    "download_remove",
                    {"hash": download.torrent_hash},
                    job=job,
                    operation_id=f"release-remove-{download.id}-{attempt}",
                    timeout=20,
                )
                removed = False
                if download.id.startswith("dl-"):
                    result = await nodes.execute(
                        node_id,
                        "discard_staging",
                        {"root_id": "staging", "path": download.id},
                        job=job,
                        operation_id=f"release-discard-{download.id}-{attempt}",
                        timeout=60,
                    )
                    removed = bool(result.get("removed"))
            except NodeError as exc:
                logger.warning("ended request's transfer %s not released yet: %s", download.id, exc)
                continue
            metadata = {**download.metadata, "released_at": now}
            organized = download.status == DownloadStatus.ORGANIZED
            await service.storage.update_download(
                download.id,
                status=download.status if organized else DownloadStatus.ERROR,
                metadata=metadata,
                error_message=download.error_message if organized else (
                    "Request ended; its unfinished files were cleared." if removed
                    else "Request ended; removed from the download app."
                ),
            )
            logger.info("released ended request's transfer %s (staging removed: %s)", download.id, removed)


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
        error_message="Cancelled; its files are kept for a day in case it is asked for again." if action == "remove" else "",
    )
    if not any(
        d.metadata.get("desired_control")
        for d in service.storage.get_all_downloads()
        if d.metadata.get("job_id") == job.id
    ):
        job.state_line = {
            JobStatus.PAUSED: "Paused. Downloads stopped.",
            JobStatus.ACTIVE: "Resumed.",
            JobStatus.ABANDONED: "Cancelled. Anything already in your library stays.",
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
        "pause": "Pausing…",
        "resume": "Resuming…",
        "cancel": "Cancelling…",
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
            "pause": "Paused. Waiting for storage to stop the downloads.",
            "resume": "Resumed. Waiting for storage to restart the downloads.",
            "cancel": "Cancelled. Waiting for storage to stop the downloads.",
        }[action]
    else:
        job.state_line = {
            "pause": "Paused. Downloads stopped.",
            "resume": "Resumed.",
            "cancel": "Cancelled. Anything already in your library stays.",
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
