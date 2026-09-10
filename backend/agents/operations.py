"""Bounded operational history. Fixed summaries; private work stays private.

This is an event history, not a copy of Python logs, model messages or login
sessions. No exception strings, release names, paths, URLs or credentials enter
its detail payload. Repeats group within five-minute windows when reading a
fixed event-id snapshot. Retain at most 50,000 events / 90 days.
"""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

# code: category, severity, readable summary
EVENTS = {
    "request_submitted": (
        "request",
        "info",
        "Request submitted; Sparrow will check what is needed.",
    ),
    "request_active": ("request", "info", "Request resumed; Sparrow will check again."),
    "request_paused": ("request", "info", "Request paused."),
    "request_complete": (
        "request",
        "success",
        "Request complete; the requested media has been verified.",
    ),
    "request_abandoned": (
        "request",
        "warning",
        "Request stopped; review it or try again.",
    ),
    "request_failed": (
        "request",
        "error",
        "Request needs attention; review it and try again.",
    ),
    "download_started": ("download", "info", "Download started."),
    "download_stalled": (
        "download",
        "warning",
        "Download stalled; Sparrow has been asked to check the source.",
    ),
    "download_recovered": ("download", "success", "Download is making progress again."),
    "download_completed": (
        "download",
        "success",
        "Download completed; the files still need checking.",
    ),
    "download_failed": (
        "download",
        "error",
        "Download failed; review the request for recovery.",
    ),
    "media_checking": ("import", "info", "Checking and matching the downloaded files."),
    "media_organized": ("import", "success", "Verified media added to the library."),
    "import_preview": (
        "import",
        "info",
        "Folder checked; matching choices are ready to review.",
    ),
    "import_failed": (
        "import",
        "error",
        "Import could not finish; scan again and review matching choices.",
    ),
    "import_matched": ("import", "success", "Imported and matched to the library."),
    "import_corrected": (
        "import",
        "success",
        "Library matching updated; watch progress preserved.",
    ),
    "storage_online": ("storage", "success", "Storage is available again."),
    "storage_offline": (
        "storage",
        "warning",
        "Storage is unavailable; reconnect the device or check its folders.",
    ),
    "client_down": (
        "download",
        "error",
        "The download app is unavailable; requests will wait for it.",
    ),
    "client_recovered": ("download", "success", "The download app is available again."),
    "playback_failed": (
        "playback",
        "error",
        "Playback could not be prepared or delivered; reopen the title and try again.",
    ),
    "playback_recovered": ("playback", "success", "Playback is available again."),
    "captions_failed": (
        "subtitle",
        "error",
        "The subtitle track could not be prepared; try subtitle repair in the player.",
    ),
    "captions_recovered": (
        "subtitle",
        "success",
        "The subtitle track is available again.",
    ),
    "subtitle_needs_attention": (
        "subtitle",
        "warning",
        "Subtitle preparation needs attention; review or retry repair in the player.",
    ),
    "subtitle_failed": (
        "subtitle",
        "error",
        "Subtitle preparation failed; retry repair in the player.",
    ),
    "subtitle_review_pending": (
        "subtitle",
        "warning",
        "Subtitle quality review did not finish; check the review service and retry from the player.",
    ),
    "subtitle_ready": ("subtitle", "success", "Subtitles prepared and checked."),
}


class Operations:
    def __init__(self, data_dir):
        from .migrations import prepare

        prepare(data_dir)
        self.path = Path(data_dir) / "sparrow.db"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS operational_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
                    code TEXT NOT NULL, category TEXT NOT NULL, severity TEXT NOT NULL,
                    user_id TEXT NOT NULL, library_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    item_id TEXT NOT NULL, title TEXT NOT NULL, subject TEXT NOT NULL,
                    fingerprint TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS operational_scope ON operational_events(user_id, library_id, id);
                CREATE INDEX IF NOT EXISTS operational_time ON operational_events(ts);
                CREATE TABLE IF NOT EXISTS operational_state (
                    subject TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(
        self,
        code,
        *,
        user_id="",
        library_id="",
        job_id="",
        item_id="",
        title="",
        subject="",
        db=None,
    ):
        if db is None:
            with self.connect() as connection:
                return self.record(
                    code,
                    user_id=user_id,
                    library_id=library_id,
                    job_id=job_id,
                    item_id=item_id,
                    title=title,
                    subject=subject,
                    db=connection,
                )
        category, severity, _ = EVENTS[code]
        context = [
            str(v) for v in (user_id, library_id, job_id, item_id, title, subject)
        ]
        fingerprint = hashlib.sha256(json.dumps([code, *context]).encode()).hexdigest()
        now = time.time()
        result = db.execute(
            "INSERT INTO operational_events(ts,code,category,severity,user_id,library_id,job_id,item_id,title,subject,fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now, code, category, severity, *context, fingerprint),
        )
        db.execute(
            "DELETE FROM operational_events WHERE id<=? OR ts<?",
            (result.lastrowid - 50000, now - 90 * 86400),
        )
        db.execute("DELETE FROM operational_state WHERE updated<?", (now - 90 * 86400,))
        return result.lastrowid

    def observe(self, subject, value, code, *, initial=False, **context):
        """Record a measured transition, never every heartbeat / successful read."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT value FROM operational_state WHERE subject=?", (subject,)
            ).fetchone()
            db.execute(
                "INSERT INTO operational_state VALUES(?,?,?) ON CONFLICT(subject) DO UPDATE SET value=excluded.value,updated=excluded.updated",
                (subject, value, time.time()),
            )
            if (previous and previous["value"] != value) or (not previous and initial):
                self.record(code, subject=subject, db=db, **context)

    def job(self, code, job, **kwargs):
        return self.record(
            code,
            user_id=job.user_id,
            library_id=job.library_id,
            job_id=job.id,
            title=job.title,
            **kwargs,
        )

    def download(self, download):
        code = {
            "downloading": "download_started",
            "completed": "download_completed",
            "error": "download_failed",
            "organized": "media_organized",
        }.get(download.status.value)
        if not code:
            return
        with self.connect() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='jobs'"
            ).fetchone():
                return
            row = db.execute(
                "SELECT data FROM jobs WHERE id=?",
                (download.metadata.get("job_id", ""),),
            ).fetchone()
        if row:
            from .models import Job

            job = Job.from_dict(json.loads(row["data"]))
            self.observe(
                "download:" + download.id,
                download.status.value,
                code,
                initial=True,
                user_id=job.user_id,
                library_id=job.library_id,
                job_id=job.id,
                title=job.title,
            )

    def media(self, code, user, asset, storage, *, observed=None):
        item = storage.get_library_item(asset["item_id"])
        context = dict(
            user_id=user["id"],
            library_id=asset["node_id"],
            item_id=asset["item_id"],
            title=item.title if item else "",
        )
        if observed:
            subject, value = observed
            self.observe(
                f'{subject}:{user["id"]}:{asset["id"]}',
                value,
                code,
                initial=value == "failed",
                **context,
            )
        else:
            self.record(code, subject=asset["id"], **context)

    def page(
        self,
        user,
        *,
        category="",
        severity="",
        q="",
        job_id="",
        since=0,
        until=0,
        page=1,
        limit=25,
        snapshot=None,
    ):
        clauses, args = [], []
        if user["role"] != "admin":
            clauses.append(
                "(user_id=? OR (user_id='' AND library_id!='' AND category IN ('storage','import')))"
            )
            args.append(user["id"])
        # Apply current permissions before grouping, counting and searching.
        scope = user.get("library_scope")
        if scope is not None and user["role"] != "admin":
            clauses.append("library_id IN (SELECT value FROM json_each(?))")
            args.append(json.dumps(scope))
        for key, value in (
            ("category", category),
            ("severity", severity),
            ("job_id", job_id),
        ):
            if value:
                clauses.append(f"{key}=?")
                args.append(value)
        if q:
            clauses.append(
                "(instr(lower(title),lower(?))>0 OR instr(lower(job_id),lower(?))>0)"
            )
            args.extend([q, q])
        if since:
            clauses.append("ts>=?")
            args.append(since)
        if until:
            clauses.append("ts<=?")
            args.append(until)
        with self.connect() as db:
            db.execute("BEGIN")
            if snapshot is None:
                snapshot = db.execute(
                    "SELECT coalesce(max(id),0) FROM operational_events"
                ).fetchone()[0]
            clauses.append("id<=?")
            args.append(snapshot)
            grouped = (
                "SELECT *,max(id) AS last_id,min(ts) AS first_ts,max(ts) AS last_ts,count(*) AS repeats "
                "FROM operational_events WHERE "
                + " AND ".join(clauses)
                + " GROUP BY fingerprint,cast(ts/300 AS INTEGER)"
            )
            total = db.execute(
                "SELECT count(*) FROM (" + grouped + ")", args
            ).fetchone()[0]
            rows = db.execute(
                grouped + " ORDER BY last_id DESC LIMIT ? OFFSET ?",
                [*args, limit, (page - 1) * limit],
            ).fetchall()
        entries = []
        for row in rows:
            entry = {
                key: row[key]
                for key in (
                    "category",
                    "severity",
                    "job_id",
                    "title",
                    "first_ts",
                    "last_ts",
                    "repeats",
                )
            }
            entry.update(id=row["last_id"], summary=EVENTS[row["code"]][2], action=None)
            if row["item_id"]:
                entry["action"] = {
                    "label": "Open title",
                    "href": "/items/" + quote(row["item_id"], safe=""),
                }
            elif row["job_id"]:
                entry["action"] = {
                    "label": "View request",
                    "href": "/activity?request=" + quote(row["job_id"], safe=""),
                }
            elif user["role"] == "admin":
                entry["action"] = {
                    "label": (
                        "Check storage"
                        if row["category"] in ("import", "storage")
                        else "Server settings"
                    ),
                    "href": (
                        "/settings/storage"
                        if row["category"] in ("import", "storage")
                        else "/settings/server"
                    ),
                }
            if user["role"] == "admin":
                entry["detail"] = {
                    "event": row["code"],
                    "storage": row["library_id"],
                    "subject": row["subject"],
                }
            entries.append(entry)
        return {
            "entries": entries,
            "total": total,
            "page": page,
            "limit": limit,
            "snapshot": snapshot,
        }


def install_operations(app, storage):
    router = APIRouter(prefix="/api/v1")

    @router.get("/logs")
    def logs(
        request: Request,
        category: Literal[
            "", "request", "download", "import", "storage", "playback", "subtitle"
        ] = "",
        severity: Literal["", "info", "success", "warning", "error"] = "",
        q: str = Query(default="", max_length=300),
        job_id: str = Query(default="", max_length=100),
        since: float = Query(default=0, ge=0, allow_inf_nan=False),
        until: float = Query(default=0, ge=0, allow_inf_nan=False),
        page: int = Query(default=1, ge=1, le=2001),
        limit: int = Query(default=25, ge=1, le=100),
        snapshot: int | None = Query(default=None, ge=0, le=9223372036854775807),
    ):
        if since and until and since > until:
            raise HTTPException(422, "The end time must be after the start time.")
        return storage.operations.page(
            request.state.user,
            category=category,
            severity=severity,
            q=q.strip(),
            job_id=job_id,
            since=since,
            until=until,
            page=page,
            limit=limit,
            snapshot=snapshot,
        )

    app.include_router(router)
