"""What each computer is for, and whether it is doing it.

A computer is this server or a paired Sparrow Node. The household gives it
jobs: Storage (keeps films and series), Downloads (fetches new ones) and
Subtitles (listens to soundtracks to check, time and write subtitles).
Playback is not a choice: a computer that keeps files plays them, and its row
says whether it can.

Sparrow remembers which jobs are wanted; each computer reports what actually
works. Health is the gap between the two, in plain words, and placement reads
the same answer the Storage page shows.
"""

from __future__ import annotations

import json
import time

JOBS = ("storage", "downloads", "subtitles")
GB = 1024**3
ONLINE_SECONDS = 60


def _table(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS computer_jobs(node_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
    )


def default_jobs(node_id: str, capabilities: dict | None = None) -> dict:
    """Every job a computer can do starts on; Subtitles only where it pays.

    Listening to soundtracks is heavy, so it starts on for this server and for a
    computer with a graphics card, and stays off on an ordinary paired computer.
    """
    speech = (capabilities or {}).get("speech") or {}
    return {
        "storage": True,
        "downloads": True,
        "subtitles": node_id == "local" or bool(speech.get("gpu")),
    }


def wanted(accounts, node_id: str, capabilities: dict | None = None) -> dict:
    with accounts.connect() as db:
        _table(db)
        row = db.execute(
            "SELECT data FROM computer_jobs WHERE node_id=?", (node_id,)
        ).fetchone()
    jobs = default_jobs(node_id, capabilities)
    if row:
        saved = json.loads(row["data"])
        jobs.update({k: bool(v) for k, v in saved.items() if k in JOBS})
    return jobs


def set_wanted(accounts, node_id: str, changes: dict, capabilities: dict | None = None) -> dict:
    unknown = set(changes) - set(JOBS)
    if unknown:
        raise ValueError("Choose Storage, Downloads or Subtitles.")
    jobs = {**wanted(accounts, node_id, capabilities), **{k: bool(v) for k, v in changes.items()}}
    with accounts.connect() as db:
        _table(db)
        db.execute(
            "INSERT INTO computer_jobs VALUES (?, ?) ON CONFLICT(node_id) DO UPDATE SET data=excluded.data",
            (node_id, json.dumps(jobs, sort_keys=True)),
        )
    return jobs


def forget(accounts, node_id: str) -> None:
    with accounts.connect() as db:
        _table(db)
        db.execute("DELETE FROM computer_jobs WHERE node_id=?", (node_id,))


# ─── Health ──────────────────────────────────────────────────────────


def size(value) -> str:
    """Sizes as people say them: 850 GB, 61 GB, 1.8 TB."""
    value = value or 0
    if value >= 1e12:
        return f"{value / 1e12:.1f} TB"
    if value >= 1e10:
        return f"{round(value / 1e9)} GB"
    if value >= 1e9:
        return f"{value / 1e9:.1f} GB"
    return f"{round(value / 1e6)} MB"


def nearly_full(free, total) -> bool:
    return free is not None and total and free < max(total * 0.05, 10e9)


def _libraries(capabilities):
    return [
        r
        for r in capabilities.get("roots") or []
        if isinstance(r, dict) and str(r.get("id", "")).startswith("library")
    ]


def _staging(capabilities):
    return next(
        (r for r in capabilities.get("roots") or [] if isinstance(r, dict) and r.get("id") == "staging"),
        None,
    )


def _disks(roots):
    """Library folders on one disk count once."""
    seen, out = set(), []
    for root in roots:
        if not root.get("available") or not root.get("total_bytes"):
            continue
        key = root.get("disk") or (root["total_bytes"], round((root.get("free_bytes") or 0) / 1e9))
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def row(job, on, state, line, fix=None):
    """One line of a computer's card: a job, whether it works, and what to do."""
    return {"job": job, "on": on, "state": state, "line": line, "fix": fix}


def health(node: dict, jobs: dict) -> dict:
    """A computer and each of its jobs in plain words.

    States: ready (working), attention (on but not working, with a fix),
    waiting (the computer is offline or still setting up) and off.
    Fixes: "folders" (choose folders), "repair" (run the setup command again),
    "download_app" (this server's download app settings).
    """
    caps = node.get("capabilities") or {}
    local = node.get("id") == "local"
    online = bool(node.get("online")) and not node.get("disabled")
    if node.get("disabled"):
        status = {"state": "revoked", "line": "Revoked"}
    elif online:
        status = {"state": "online", "line": "Online"}
    else:
        status = {"state": "offline", "line": "Offline", "last_seen": node.get("last_seen") or 0}
    repair = "download_app" if local else "repair"
    rows = []

    # Storage, and Playback beside it: a computer that keeps files plays them.
    libraries = _libraries(caps)
    disks = _disks(libraries)
    free = sum(r.get("free_bytes") or 0 for r in disks)
    total = sum(r.get("total_bytes") or 0 for r in disks)
    if not jobs.get("storage"):
        kept = node.get("library_bytes") or 0
        rows.append(
            row("storage", False, "off", f"Off. {size(kept)} stays here." if kept else "Off")
        )
    elif not online:
        rows.append(row("storage", True, "waiting", "Offline"))
    elif not libraries:
        rows.append(row("storage", True, "attention", "Choose a library folder", "folders"))
    elif not all(r.get("available") for r in libraries):
        rows.append(row("storage", True, "attention", "Library folder not found", "folders"))
    elif any(r.get("writable") is False for r in libraries):
        rows.append(row("storage", True, "attention", "Can't save here", "folders"))
    elif disks and all(nearly_full(r.get("free_bytes"), r.get("total_bytes")) for r in disks):
        rows.append(row("storage", True, "attention", f"Nearly full. {size(free)} free"))
    else:
        rows.append(row("storage", True, "ready", f"{size(free)} free of {size(total)}" if total else "Ready"))
    if jobs.get("storage") or libraries:
        if not online:
            rows.append(row("playback", True, "waiting", "Offline"))
        elif not caps.get("transcode") or not caps.get("probe"):
            rows.append(
                row("playback", True, "attention", "Some files won't play here. Media tools missing", repair if not local else None)
            )
        else:
            rows.append(row("playback", True, "ready", "Ready"))

    # Downloads.
    staging = _staging(caps)
    downloader = caps.get("downloader_state") or ("running" if caps.get("download") else "none")
    if not jobs.get("downloads"):
        rows.append(row("downloads", False, "off", "Off"))
    elif not online:
        rows.append(row("downloads", True, "waiting", "Offline"))
    elif downloader in ("none", "missing"):
        rows.append(row("downloads", True, "attention", "No download app", repair))
    elif downloader == "starting":
        rows.append(row("downloads", True, "waiting", "Starting the download app"))
    elif downloader == "failed":
        rows.append(row("downloads", True, "attention", "Download app stopped", repair))
    elif not staging or not staging.get("available"):
        rows.append(row("downloads", True, "attention", "Choose an incoming folder", "folders"))
    elif staging.get("writable") is False:
        rows.append(row("downloads", True, "attention", "Can't save to the incoming folder", "folders"))
    else:
        rows.append(row("downloads", True, "ready", "Ready"))

    # Subtitles.
    speech = caps.get("speech")
    if speech is None:
        # Computers from before speech reporting: the model is the best sign.
        speech = {"ready": bool(caps.get("subtitle_model")), "device": "cpu"}
    if not jobs.get("subtitles"):
        rows.append(row("subtitles", False, "off", "Off"))
    elif not online:
        rows.append(row("subtitles", True, "waiting", "Offline"))
    elif speech.get("installing"):
        progress = speech.get("progress")
        rows.append(
            row("subtitles", True, "waiting", f"Setting up. {int(progress)}%" if progress else "Setting up")
        )
    elif speech.get("error"):
        rows.append(row("subtitles", True, "attention", str(speech["error"])[:120], repair))
    elif not speech.get("ready"):
        rows.append(row("subtitles", True, "waiting" if not local else "attention", "Setting up" if not local else "Speech model missing"))
    elif speech.get("gpu") and speech.get("device") == "cuda":
        rows.append(row("subtitles", True, "ready", f"Uses the {speech['gpu']}"))
    elif speech.get("gpu"):
        # The card is there but failed, so the processor listens for now.
        rows.append(row("subtitles", True, "ready", "Uses the processor. The graphics card didn't work"))
    else:
        rows.append(row("subtitles", True, "ready", "Uses the processor"))

    attention = [r for r in rows if r["state"] == "attention"]
    return {
        **status,
        "runs_sparrow": local,
        "jobs": jobs,
        "rows": rows,
        "attention": len(attention),
    }


def can(node: dict, jobs: dict, job: str) -> bool:
    """Whether a computer is on for a job and able to do it right now."""
    if not jobs.get(job):
        return False
    report = health(node, jobs)
    return any(r["job"] == job and r["state"] == "ready" for r in report["rows"])


def seen_ago(last_seen: float, now: float | None = None) -> str:
    seconds = max(0, (now or time.time()) - (last_seen or 0))
    if not last_seen:
        return "never"
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)} days"
