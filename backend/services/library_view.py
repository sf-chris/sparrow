"""
The Library projection: organized inventory PLUS pending v3 work.

The Library the user sees is not "what's on disk" — it is "everything I've
asked for, and where each piece is". A newly requested episode appears here
immediately and moves through plain-language states until it is ready:

    requested -> queued -> downloading -> verifying -> ready

Titles whose work is paused or has visibly gone wrong surface as
"needs attention" so the default view explains itself.
"""
from __future__ import annotations

import time
from typing import Optional

from ..models import DownloadStatus, MediaType
from ..agents.models import JobStatus

# Plain-language episode/title states, in order of advancement.
REQUESTED = "requested"
QUEUED = "queued"
DOWNLOADING = "downloading"
VERIFYING = "verifying"
READY = "ready"
PAUSED = "paused"

# How long a completed-but-unpolled stat is still presented as live.
STALE_AFTER_SECONDS = 120


def _job_pending_state(job, downloads: list) -> str:
    """The plain-language state for work a job hasn't finished yet."""
    if job.status == JobStatus.PAUSED:
        return PAUSED
    statuses = {dl.status for dl in downloads}
    if DownloadStatus.COMPLETED in statuses or DownloadStatus.ORGANIZING in statuses:
        return VERIFYING
    if DownloadStatus.DOWNLOADING in statuses or DownloadStatus.SEEDING in statuses:
        return DOWNLOADING
    if DownloadStatus.QUEUED in statuses or DownloadStatus.PAUSED in statuses:
        return QUEUED
    return REQUESTED


def _needs_attention(job, downloads: list) -> bool:
    """Paused work, or errors with nothing else moving, need the user."""
    if job.status == JobStatus.PAUSED:
        return True
    if job.status != JobStatus.ACTIVE:
        return False
    moving = any(dl.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING,
                               DownloadStatus.SEEDING, DownloadStatus.COMPLETED,
                               DownloadStatus.ORGANIZING)
                 for dl in downloads)
    errored = any(dl.status == DownloadStatus.ERROR and
                  "cancelled" not in (dl.error_message or "")
                  for dl in downloads)
    return errored and not moving


def _transfer_summary(downloads: list, now: Optional[float] = None) -> dict:
    """Bytes-weighted progress across a job's live transfers."""
    now = now or time.time()
    live = [dl for dl in downloads
            if dl.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING,
                             DownloadStatus.SEEDING, DownloadStatus.PAUSED)]
    if not live:
        return {"progress": None, "speed_bps": 0, "eta_seconds": None,
                "stats_updated_at": None, "stale": False}
    total = sum(dl.size_bytes for dl in live)
    if total:
        progress = sum(dl.size_bytes * dl.progress for dl in live) / total
    else:
        progress = sum(dl.progress for dl in live) / len(live)
    etas = [dl.eta_seconds for dl in live if dl.eta_seconds and dl.eta_seconds >= 0]
    updated = [dl.stats_updated_at for dl in live if dl.stats_updated_at]
    last_updated = max(updated) if updated else None
    return {
        "progress": round(progress, 4),
        "speed_bps": sum(dl.download_speed for dl in live),
        "eta_seconds": max(etas) if etas else None,
        "stats_updated_at": last_updated,
        "stale": bool(last_updated is None or now - last_updated > STALE_AFTER_SECONDS),
    }


def build_library_view(storage, agent_store, now: Optional[float] = None) -> list[dict]:
    """Organized inventory + active/paused jobs, merged per title."""
    now = now or time.time()
    entries: dict[tuple[str, int], dict] = {}

    def entry_for(media_type: str, tmdb_id: Optional[int], title: str,
                  year=None, poster_path: str = "") -> dict:
        key = (media_type, tmdb_id or 0)
        if key not in entries:
            entries[key] = {
                "tmdb_id": tmdb_id,
                "media_type": media_type,
                "title": title,
                "year": year,
                "poster_path": poster_path,
                "in_library": False,
                "library_item_id": None,
                "episodes": {},          # {"1": {"3": {"state": ..., "quality": ...}}}
                "ready_count": 0,
                "pending_count": 0,
                "state": READY,
                "needs_attention": False,
                "job": None,
                "transfers": None,
                "size_bytes": 0,
            }
        return entries[key]

    for item in storage.get_library():
        e = entry_for(item.media_type.value, item.tmdb_id, item.title,
                      item.year, item.poster_path)
        e["in_library"] = True
        e["library_item_id"] = item.id
        e["size_bytes"] = item.size_bytes
        if item.media_type == MediaType.MOVIE:
            e["ready_count"] = 1
            e["quality"] = item.metadata.get("quality", "")
            e["verified"] = bool(item.metadata.get("verified"))
        else:
            for season, eps in item.episodes.items():
                for episode, info in eps.items():
                    # On disk means watchable — "verifying" is reserved for
                    # landed downloads the Media Agent is still processing.
                    e["episodes"].setdefault(str(season), {})[str(episode)] = {
                        "state": READY,
                        "quality": info.get("quality", ""),
                        "verified": bool(info.get("verified")),
                        "updated_at": info.get("added_at"),
                    }
                    e["ready_count"] += 1

    downloads = storage.get_all_downloads()
    for job in agent_store.get_jobs():
        if job.status not in (JobStatus.ACTIVE, JobStatus.PAUSED):
            continue
        job_downloads = [dl for dl in downloads
                         if dl.metadata.get("job_id") == job.id]
        pending_state = _job_pending_state(job, job_downloads)
        e = entry_for(job.media_type, job.tmdb_id, job.title, job.year,
                      job.poster_path)
        e["job"] = {
            "id": job.id,
            "status": job.status.value,
            "state_line": job.state_line,
            "next_wake_at": job.next_wake_at,
            "updated_at": job.updated_at,
            "origin": job.origin,
        }
        e["transfers"] = _transfer_summary(job_downloads, now)
        e["needs_attention"] = _needs_attention(job, job_downloads)
        if job.media_type == "movie":
            if not e["ready_count"]:
                e["pending_count"] = 1
                e["state"] = pending_state
        else:
            for season, eps in job.wanted_episodes.items():
                for episode in eps:
                    existing = e["episodes"].get(str(season), {}).get(str(episode))
                    if existing and existing["state"] == READY:
                        continue
                    e["episodes"].setdefault(str(season), {})[str(episode)] = {
                        "state": pending_state,
                        "quality": "",
                        "updated_at": job.updated_at,
                    }
                    e["pending_count"] += 1

    out = []
    for e in entries.values():
        if e["media_type"] == "tv":
            states = [ep["state"] for season in e["episodes"].values()
                      for ep in season.values()]
            e["ready_count"] = sum(1 for s in states if s == READY)
            e["pending_count"] = sum(1 for s in states if s not in (READY,))
            active_states = [s for s in states if s != READY]
            order = [PAUSED, VERIFYING, DOWNLOADING, QUEUED, REQUESTED]
            e["state"] = next((s for s in order if s in active_states), READY)
        elif e["pending_count"]:
            pass  # movie state already set from the job
        else:
            e["state"] = READY if e["ready_count"] else e["state"]
        out.append(e)

    out.sort(key=lambda e: (e["state"] == READY, e["title"].lower()))
    return out
