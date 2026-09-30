"""Durable subtitle preparation, dialogue-timed correction and agent review.

A task finds a candidate track and makes it playable straight away. For
foreign-language dialogue, or on request, the storage node builds
caption-independent speech evidence; tools measure the track against the voice
and apply the least invasive timing correction. When the household asks for
checking, a reviewer model reads the evidence page by page and the tools enforce
its verdict. A track that fails is set aside and the next candidate is tried.
Originals are never modified and personal offsets stay private.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from . import subtitle_review as pages_
from .account_api import administrator
from .media_state import language_code
from .models import AgentKind, AgentSession, CaseState, Event, SessionStatus
from .node_executor import NodeError, canonical
from .runtime import AgentSpec, ToolDef, ToolError
from .subtitle_provider import SubtitleProvider
from .subtitle_sync import (
    EARLY_TOLERANCE,
    LATE_TOLERANCE,
    TARGET_OFFSET,
    correction as timing_correction,
    estimate,
)

ACTIVE = {"queued", "finding", "aligning", "measuring", "reviewing"}
DEFAULT_REVIEW_MODEL = "claude-opus-5-5"
QUEUE_LIMIT = 500
MAX_CANDIDATES = 4
EVIDENCE_SLICE = 540
REVIEW_WAKES = 6


def review_model():
    return os.getenv("SPARROW_SUBTITLE_MODEL") or DEFAULT_REVIEW_MODEL


def timing_summary(measured):
    keys = (
        "measurable", "consistent", "offset", "offset_ci95", "line_spread",
        "drift_over_track", "paired_captions", "caption_count", "anchor_match",
        "prominence", "reason",
    )
    return {k: measured[k] for k in keys if k in measured}


def in_window(measured):
    error = measured.get("offset", 0) - TARGET_OFFSET
    return (
        measured.get("measurable")
        and measured.get("consistent")
        and -EARLY_TOLERANCE <= error <= LATE_TOLERANCE
    )


class Subtitles:
    def __init__(self, accounts, nodes, catalogue, get_service):
        self.accounts, self.nodes, self.catalogue, self.get_service = (
            accounts,
            nodes,
            catalogue,
            get_service,
        )
        self.tasks = {}
        self.node_locks = {}
        self.evidence_locks = {}
        self._documents = {}
        self._measures = {}
        with accounts.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS subtitle_tasks(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,asset_id TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS subtitle_tracks(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,node_id TEXT NOT NULL,language TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS subtitle_offsets(user_id TEXT NOT NULL,track_id TEXT NOT NULL,seconds REAL NOT NULL,PRIMARY KEY(user_id,track_id));
                CREATE TABLE IF NOT EXISTS subtitle_provider(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL);
                INSERT OR IGNORE INTO subtitle_provider VALUES(1,'{}');
            """)

    # ─── Records ──────────────────────────────────────────────────────────

    def provider(self):
        with self.accounts.connect() as db:
            row = db.execute("SELECT data FROM subtitle_provider WHERE id=1").fetchone()
        return json.loads(row["data"])

    def task(self, identity):
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT * FROM subtitle_tasks WHERE id=?", (identity,)
            ).fetchone()
        return {**dict(row), "data": json.loads(row["data"])} if row else None

    def track(self, identity):
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT * FROM subtitle_tracks WHERE id=?", (identity or "",)
            ).fetchone()
        return {**dict(row), "data": json.loads(row["data"])} if row else None

    def save_track(self, identity, state=None, **values):
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT state,data FROM subtitle_tracks WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                raise ToolError("The subtitle track no longer exists.")
            data = {**json.loads(row["data"]), **values}
            db.execute(
                "UPDATE subtitle_tracks SET state=?,data=? WHERE id=?",
                (state or row["state"], canonical(data), identity),
            )

    def update(self, task, state, message="", code=None, **values):
        data = {**task["data"], **values, "message": message}
        with self.accounts.connect() as db:
            changed = db.execute(
                "UPDATE subtitle_tasks SET state=?,data=?,updated=? WHERE id=? AND (state!='cancelled' OR ?='cancelled')",
                (state, canonical(data), time.time(), task["id"], state),
            )
            if not changed.rowcount:
                raise ToolError("This subtitle request was cancelled.")
        previous_state = task["state"]
        task["state"], task["data"] = state, data
        if state != previous_state and state in (
            "ready",
            "needs_attention",
            "failed",
            "review_pending",
        ):
            user = self.accounts.user(task["user_id"])
            asset = self.catalogue.asset(user, task["asset_id"]) if user else None
            if asset:
                self.catalogue.storage.operations.media(
                    code or "subtitle_" + state, user, asset, self.catalogue.storage
                )

    def authority(self, task):
        user = self.accounts.user(task["user_id"])
        asset = (
            self.catalogue.asset(user, task["asset_id"])
            if user and not user["disabled"]
            else None
        )
        current = self.task(task["id"])
        if (
            not current
            or current["state"] == "cancelled"
            or not asset
            or asset["facts"]["version"] != task["data"]["version"]
        ):
            raise ToolError(
                "This subtitle request ended, lost access or refers to an older media copy."
            )
        job_id = task["data"].get("job_id")
        if job_id:
            job = self.get_service().store.get_job(job_id)
            if (
                not job
                or job.status.value != "active"
                or job.revision != task["data"].get("job_revision")
            ):
                raise ToolError("The media request changed; subtitle work has stopped.")
        return user, asset

    def tracks(self, user, asset_id):
        asset = self.catalogue.asset(user, asset_id)
        if not asset:
            return []
        with self.accounts.connect() as db:
            rows = db.execute(
                "SELECT t.*,o.seconds FROM subtitle_tracks t LEFT JOIN subtitle_offsets o ON o.track_id=t.id AND o.user_id=? WHERE t.asset_id=? ORDER BY created DESC",
                (user["id"], asset_id),
            ).fetchall()
        out = []
        for row in rows:
            data = json.loads(row["data"])
            if data["media_version"] != asset["facts"]["version"]:
                continue
            if row["state"] == "superseded":
                continue  # A newer copy of the same source replaced it.
            out.append(
                {
                    "id": row["id"],
                    "language": row["language"],
                    "kind": row["kind"],
                    "state": row["state"],
                    "offset": row["seconds"] or 0,
                    "audio_index": data.get("audio_index"),
                    "source": data["source"],
                    "source_id": data.get("source_id"),
                    "unchanged": data.get("unchanged", True),
                    "timing": data.get("timing"),
                    "timing_adjusted": bool(data.get("correction")),
                    "review": data.get("review"),
                    "sync_checked": bool((data.get("review") or {}).get("approved")),
                    "url": f"/api/v1/subtitles/tracks/{row['id']}.vtt",
                    "original_url": (
                        f"/api/v1/subtitles/tracks/{row['id']}.vtt?original=true"
                        if data.get("original_version")
                        else None
                    ),
                }
            )
        return out

    def choose_audio(self, user, asset, audio_tracks, preferred):
        if preferred == "original":
            item = self.catalogue.item(user, asset["item_id"])
            title = (
                self.catalogue.cached_title(item.media_type.value, item.tmdb_id)
                if item and item.tmdb_id
                else {}
            )
            preferred = (title or {}).get("original_language", "")
        return next(
            (
                t["index"]
                for t in audio_tracks
                if preferred and language_code(t["language"]) == language_code(preferred)
            ),
            next(
                (t["index"] for t in audio_tracks if t["default"]),
                audio_tracks[0]["index"] if audio_tracks else None,
            ),
        )

    def satisfies(self, user, asset_id, preferences):
        asset = self.catalogue.asset(user, asset_id)
        if not asset:
            return False
        chosen = self.choose_audio(
            user, asset, asset["facts"]["audio_tracks"], preferences["audio_pref"]
        )
        # Request snapshots from before optional checking keep their requirement.
        return any(
            t["state"] == "ready"
            and t["audio_index"] == chosen
            and t["language"] in preferences["subtitle_languages"]
            and t["kind"] == preferences["subtitle_kind"]
            and (not preferences.get("verify_subtitles", True) or t["sync_checked"])
            for t in self.tracks(user, asset_id)
        )

    # ─── Requests ─────────────────────────────────────────────────────────

    async def enqueue(
        self,
        user,
        asset_id,
        *,
        upload=None,
        language=None,
        kind=None,
        audio_index=None,
        job=None,
        preferences=None,
        verify=None,
        repair=False,
    ):
        asset = self.catalogue.asset(user, asset_id)
        if not asset:
            raise ToolError("This media is not available to your account.")
        prefs = preferences or (
            job.preferences if job else self.accounts.resolve(user["id"])
        )
        verify = (
            prefs["values"].get("verify_subtitles", True) if verify is None else verify
        )
        language = language_code(
            language or next(iter(prefs["values"]["subtitle_languages"]), "en")
        )
        if not re.fullmatch("[a-z]{2,3}", language):
            raise ToolError("Choose a subtitle language.")
        kind = kind or prefs["values"]["subtitle_kind"]
        if kind not in ("full", "sdh", "forced"):
            raise ToolError("Choose a subtitle style.")
        with self.accounts.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT * FROM subtitle_tasks WHERE asset_id=? AND user_id=? AND state IN ('queued','finding','aligning','measuring','reviewing')",
                (asset_id, user["id"]),
            ).fetchone()
            if active:
                # A running task takes on a stronger request rather than
                # silently ignoring it.
                data = json.loads(active["data"])
                stronger = {
                    k: True
                    for k, wanted in (("verify", verify), ("repair", repair))
                    if wanted and not data.get(k)
                }
                if stronger:
                    db.execute(
                        "UPDATE subtitle_tasks SET data=? WHERE id=?",
                        (canonical({**data, **stronger}), active["id"]),
                    )
                return {"id": active["id"], "state": active["state"]}
            count = db.execute(
                "SELECT COUNT(*) FROM subtitle_tasks WHERE state IN ('queued','finding','aligning','measuring','reviewing')"
            ).fetchone()[0]
            if count >= QUEUE_LIMIT:
                raise ToolError(
                    "The subtitle queue is full. Wait for current subtitle work to finish."
                )
            identity = secrets.token_hex(16)
            data = {
                "version": asset["facts"]["version"],
                "preferences": prefs,
                "language": language,
                "kind": kind,
                "audio_index": audio_index,
                "verify": verify,
                "repair": repair,
                "upload": upload,
                "attempts": [],
                "message": "Waiting to start.",
                "job_id": job.id if job else "",
                "job_revision": job.revision if job else 0,
            }
            db.execute(
                "INSERT INTO subtitle_tasks VALUES(?,?,?,?,?,?)",
                (
                    identity,
                    user["id"],
                    asset_id,
                    "queued",
                    canonical(data),
                    time.time(),
                ),
            )
        self.launch(identity)
        return {"id": identity, "state": "queued"}

    def launch(self, identity):
        if identity in self.tasks:
            return
        task = asyncio.create_task(self.run(identity))
        self.tasks[identity] = task
        task.add_done_callback(lambda _: self.tasks.pop(identity, None))

    async def notify_ready(self, task):
        service = self.get_service()
        if service and task["data"].get("job_id"):
            await service.emit(
                Event(
                    kind="subtitles_ready",
                    job_id=task["data"]["job_id"],
                    payload={
                        "description": "Subtitles are available for this request."
                    },
                )
            )

    def recover(self):
        with self.accounts.connect() as db:
            rows = db.execute(
                "SELECT id FROM subtitle_tasks WHERE state IN ('queued','finding','aligning','measuring','reviewing')"
            ).fetchall()
        for row in rows:
            self.launch(row["id"])

    async def stop(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ─── Node documents ───────────────────────────────────────────────────

    async def read(self, node_id, root_id, path, version):
        key = (node_id, root_id, path, version["size_bytes"], version.get("mtime_ns"))
        if key in self._documents:
            return self._documents[key]
        blocks = []
        for offset in range(0, version["size_bytes"], 1024 * 1024):
            part = await self.nodes.execute(
                node_id,
                "read",
                {
                    "root_id": root_id,
                    "path": path,
                    "version": version,
                    "offset": offset,
                    "length": min(1024 * 1024, version["size_bytes"] - offset),
                },
                timeout=30,
            )
            blocks.append(base64.b64decode(part["bytes"], validate=True))
        data = b"".join(blocks)
        self._documents[key] = data
        while len(self._documents) > 12:
            self._documents.pop(next(iter(self._documents)))
        return data

    async def cues(self, node_id, path, version):
        from .subtitle_worker import cues_from_text

        return cues_from_text(
            (await self.read(node_id, "cache", path, version)).decode("utf8"), "vtt"
        )

    async def speech(self, node_id, info):
        return json.loads(await self.read(node_id, "cache", info["path"], info["version"]))

    # ─── Finding a candidate ──────────────────────────────────────────────

    async def listing(self, task, user, asset):
        """Every candidate source, best first; stored so restarts resume."""
        values = task["data"]
        language, kind = values["language"], values["kind"]
        if values.get("upload"):
            sources = [
                {
                    "id": "upload",
                    "source": "upload",
                    "title": "Your subtitle file",
                    "language": language,
                    "kind": kind,
                }
            ]
            audio = values.get("audio_index")
            if audio is None:
                audio = self.choose_audio(
                    user,
                    asset,
                    asset["facts"]["audio_tracks"],
                    values["preferences"]["values"]["audio_pref"],
                )
            return sources, audio
        listing = await self.nodes.execute(
            asset["node_id"],
            "subtitle_candidates",
            {"root_id": asset["root_id"], "path": asset["path"]},
            timeout=300,
        )
        self.authority(task)
        audio = values.get("audio_index")
        if audio is None:
            audio = self.choose_audio(
                user,
                asset,
                listing["facts"]["audio_tracks"],
                values["preferences"]["values"]["audio_pref"],
            )
        local = [
            c
            for c in listing["candidates"]
            if c["language"] in (language, "und", "") and c["kind"] == kind
        ]
        # Exact language tags before untagged tracks; more cues suggest full
        # dialogue rather than signs. Order is advice, never proof.
        local.sort(key=lambda c: (c["language"] != language, -c.get("cue_count", 0)))
        provider = SubtitleProvider(self.provider())
        item = self.catalogue.item(user, asset["item_id"])
        if item and provider.settings.get("api_key"):
            try:
                local += await provider.search(
                    asset, item, language, kind, listing["movie_hash"]
                )
            except (ToolError, httpx.HTTPError):
                if not local:
                    raise
        return local, audio

    async def fetch_text(self, task, asset, candidate):
        if candidate["source"] == "upload":
            upload = task["data"]["upload"] or {}
            if not upload.get("text"):
                raise ToolError("The uploaded subtitle is no longer available; upload it again.")
            return upload["text"], upload["format"]
        if candidate["source"] == "embedded":
            result = await self.nodes.execute(
                asset["node_id"],
                "subtitle_extract",
                {
                    "root_id": asset["root_id"],
                    "path": asset["path"],
                    "version": task["data"]["version"],
                    "index": candidate["index"],
                },
                timeout=300,
            )
            return result["vtt"], "vtt"
        if candidate["source"] == "sidecar":
            if candidate["version"]["size_bytes"] > 2 * 1024 * 1024:
                raise ToolError("This local subtitle is too large.")
            raw = await self.read(
                asset["node_id"], asset["root_id"], candidate["path"], candidate["version"]
            )
            from charset_normalizer import from_bytes

            decoded = from_bytes(raw).best()
            if decoded is None:
                raise ToolError("The local subtitle text could not be decoded.")
            return str(decoded), Path(candidate["path"]).suffix[1:].lower()
        provider = SubtitleProvider(self.provider())
        return await provider.download(candidate["file_id"]), "srt"

    async def prepare(self, task, asset, candidate, text, format_name, audio, *, correction=None, suffix=""):
        operation = hashlib.sha256(
            (task["id"] + candidate["id"] + suffix).encode()
        ).hexdigest()[:32]
        result = await self.nodes.execute(
            asset["node_id"],
            "subtitle_prepare",
            {
                "root_id": asset["root_id"],
                "path": asset["path"],
                "version": task["data"]["version"],
                "text": text,
                "format": format_name,
                "task_id": operation,
                "audio_index": audio,
                "language": task["data"]["language"],
                "verify": False,
                "repair": False,
                "correction": correction,
            },
            operation_id="subs-" + operation,
            timeout=930,
        )
        self.authority(task)
        track = {
            "media_version": task["data"]["version"],
            "audio_index": audio,
            "source": candidate["source"],
            "source_id": candidate["id"],
            "title": candidate.get("title", ""),
            **result,
            "correction": correction,
        }
        with self.accounts.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO subtitle_tracks VALUES(?,?,?,?,?,?,?,?)",
                (
                    operation,
                    asset["id"],
                    asset["node_id"],
                    task["data"]["language"],
                    task["data"]["kind"],
                    "ready",
                    canonical(track),
                    time.time(),
                ),
            )
        return operation

    async def find(self, task, user, asset):
        """Prepare the next untried candidate as a playable track."""
        lock = self.node_locks.setdefault(asset["node_id"], asyncio.Lock())
        async with lock:
            self.authority(task)
            self.update(task, "finding", "Looking for subtitles in and beside the file.")
            if "candidates" not in task["data"]:
                sources, audio = await self.listing(task, user, asset)
                self.update(
                    task,
                    "finding",
                    "Preparing subtitles.",
                    candidates=sources,
                    audio_index=audio,
                )
            attempts = list(task["data"].get("attempts", []))
            tried = {a["source_id"] for a in attempts}
            for candidate in task["data"]["candidates"]:
                if len(attempts) >= MAX_CANDIDATES:
                    break
                if candidate["id"] in tried:
                    continue
                self.authority(task)
                try:
                    text, format_name = await self.fetch_text(task, asset, candidate)
                    track = await self.prepare(
                        task, asset, candidate, text, format_name, task["data"]["audio_index"]
                    )
                except (NodeError, ToolError, httpx.HTTPError, ValueError) as exc:
                    attempts.append(
                        {"source_id": candidate["id"], "outcome": "failed", "reason": str(exc)[:500]}
                    )
                    self.update(task, "finding", "Trying another subtitle file.", attempts=attempts)
                    continue
                attempts.append({"source_id": candidate["id"], "track_id": track, "outcome": "prepared"})
                self.update(
                    task,
                    "finding",
                    "Subtitles available. Checking them against the dialogue.",
                    attempts=attempts,
                    track_id=track,
                    upload=None,
                    measured=None,
                    review=None,
                    review_outcome=None,
                )
                return True
            return False

    # ─── Measuring and correcting timing ──────────────────────────────────

    def wants_analysis(self, task, asset):
        values = task["data"]
        if values.get("verify") or values.get("repair"):
            return True
        audio = next(
            (
                t
                for t in asset["facts"]["audio_tracks"]
                if t["index"] == values.get("audio_index")
            ),
            None,
        )
        spoken = language_code(audio["language"]) if audio else "und"
        # Foreign dialogue with captions is the case timing matters most for;
        # an unlabelled track may be foreign too. Same-language tracks are
        # analysed only when checking or fixing is requested.
        return spoken in ("", "und") or spoken != values["language"]

    async def evidence(self, task, asset):
        """Drive the node's resumable dialogue analysis; None if unavailable."""
        lock = self.evidence_locks.setdefault(asset["node_id"], asyncio.Lock())
        if lock.locked():
            self.update(
                task, "measuring", "Waiting to listen to the dialogue (another title is being analysed)."
            )
        async with lock:
            for _ in range(400):
                self.authority(task)
                try:
                    result = await self.nodes.execute(
                        asset["node_id"],
                        "subtitle_evidence",
                        {
                            "root_id": asset["root_id"],
                            "path": asset["path"],
                            "version": task["data"]["version"],
                            "audio_index": task["data"]["audio_index"],
                            "budget": EVIDENCE_SLICE,
                        },
                        timeout=EVIDENCE_SLICE + 660,
                    )
                except NodeError as exc:
                    return {"error": str(exc)[:500]}
                if result["state"] == "complete":
                    return result
                self.update(
                    task,
                    "measuring",
                    f"Listening to the dialogue ({round(100 * result.get('progress', 0))}%).",
                )
        return {"error": "Dialogue analysis did not finish."}

    async def measure(self, task, asset, track):
        """Measure a track against the voice and apply any justified correction.

        Returns "ok", "unmeasured", "unavailable" or "inconsistent".
        """
        self.update(task, "measuring", "Listening to the dialogue.")
        info = await self.evidence(task, asset)
        if not info or info.get("error"):
            reason = (info or {}).get("error", "Dialogue analysis is unavailable.")
            self.save_track(track["id"], timing={"measurable": False, "reason": reason})
            self.update(task, "measuring", "Dialogue analysis is unavailable.", speech=None, measured=track["id"])
            return "unavailable"
        info = {k: info[k] for k in ("path", "version", "model", "coverage")}
        self.update(task, "measuring", "Comparing caption timing with the voice.", speech=info)
        evidence = await self.speech(asset["node_id"], info)
        data = track["data"]
        original = await self.cues(asset["node_id"], data["original_path"], data["original_version"])
        measured = estimate(original, evidence)
        before = timing_summary(measured)
        if not measured.get("measurable"):
            self.save_track(track["id"], timing=before)
            self.update(task, "measuring", "Caption timing could not be measured.", measured=track["id"])
            return "unmeasured"
        if not measured["consistent"]:
            self.save_track(track["id"], timing=before)
            return "inconsistent"
        fix = timing_correction(measured)
        if not fix:
            self.save_track(track["id"], timing={**before, "within_tolerance": in_window(measured)})
            self.update(task, "measuring", "Caption timing follows the voice.", measured=track["id"])
            return "ok"
        candidate = {"id": data["source_id"], "source": data["source"], "title": data.get("title", "")}
        text = (await self.read(asset["node_id"], "cache", data["original_path"], data["original_version"])).decode("utf8")
        corrected_id = await self.prepare(
            task, asset, candidate, text, "vtt", data["audio_index"], correction=fix, suffix=":corrected"
        )
        corrected = self.track(corrected_id)
        after = estimate(
            await self.cues(asset["node_id"], corrected["data"]["path"], corrected["data"]["version"]),
            evidence,
        )
        if not in_window(after):
            # An older storage node may ignore the correction; keep the original.
            self.save_track(corrected_id, state="superseded")
            self.save_track(track["id"], timing={**before, "correction_failed": True})
            self.update(task, "measuring", "Timing could not be corrected.", measured=track["id"])
            return "ok"
        self.save_track(
            corrected_id,
            timing={**timing_summary(after), "before": before, "within_tolerance": True},
        )
        self.save_track(track["id"], state="superseded")
        self.update(
            task,
            "measuring",
            "Caption timing corrected to follow the voice.",
            track_id=corrected_id,
            measured=corrected_id,
        )
        return "ok"

    # ─── Review ───────────────────────────────────────────────────────────

    async def review_context(self, task):
        track = self.track(task["data"].get("track_id"))
        info = task["data"].get("speech")
        if not track or not info:
            return None
        node = track["node_id"]
        evidence = await self.speech(node, info)
        cues = await self.cues(node, track["data"]["path"], track["data"]["version"])
        key = (track["id"], info["version"]["size_bytes"], info["version"].get("mtime_ns"))
        if key not in self._measures:
            self._measures = {key: estimate(cues, evidence)}
        measured = self._measures[key]
        utterances = evidence["utterances"]
        state = task["data"].get("review")
        if not state or state.get("track") != track["id"]:
            state = {
                "track": track["id"],
                "pages": pages_.build_pages(utterances, cues, evidence["source"]["audio_seconds"]),
                "glosses": {},
                "judgements": {},
                "missing": {},
                "listens": [],
            }
        languages = evidence["coverage"].get("languages", {})
        spoken = max(languages, key=languages.get) if languages else ""
        return {
            "track": track,
            "evidence": evidence,
            "cues": cues,
            "measured": measured,
            "deltas": {p["cue"]: p["delta"] for p in measured.get("pairs", [])},
            "utterances": utterances,
            "state": state,
            "same_language": language_code(spoken) == task["data"]["language"],
        }

    def review_state(self, task, state, message=None):
        self.update(task, task["state"], message or task["data"].get("message", ""), review=state)

    async def review(self, task):
        """Run the reviewer until it records a verdict; None while pending."""
        service = self.get_service()
        if not service or not service.runtime._api_key_getter():
            self.update(
                task,
                "review_pending",
                "Subtitles ready. Checking them needs an Anthropic key. Add one, then try again.",
            )
            return None
        context = await self.review_context(task)
        if not context:
            return {"approved": None, "reason": "Checking needs dialogue analysis, which is unavailable for this media."}
        self.review_state(task, context["state"])
        self.attach(service)
        sessions = list(task["data"].get("review_sessions", []))
        for session_id in sessions:
            # After a restart or retry only one conversation drives the review.
            earlier = service.store.get_session(session_id)
            if earlier and earlier.status != SessionStatus.CLOSED:
                earlier.status = SessionStatus.CLOSED
                earlier.wake_at = 0
                earlier.closed_at = time.time()
                service.store.save_session(earlier)
        for _ in range(REVIEW_WAKES):
            task = self.task(task["id"])
            done = len(task["data"]["review"]["judgements"])
            total = len(task["data"]["review"]["pages"])
            session = AgentSession(
                agent=AgentKind.SUBTITLE,
                user_id=task["user_id"],
                download_id=task["id"],
                model=review_model(),
                job_id=task["data"].get("job_id", ""),
                job_revision=task["data"].get("job_revision") or 1,
                budget_scope="subtitle:" + task["id"],
            )
            session.status = SessionStatus.HIBERNATING
            service.store.save_session(session)
            sessions.append(session.id)
            self.update(
                task,
                "reviewing",
                f"Checking the captions against the dialogue ({done} of {total} pages).",
                review_session=session.id,
                review_sessions=sessions,
            )
            await service.runtime.wake(
                session.id,
                Event(
                    kind="subtitle_review",
                    payload={
                        "description": (
                            "Review this subtitle track page by page. Start with overview."
                            if not done
                            else "Continue the subtitle review. Call overview to see which pages remain."
                        )
                    },
                ),
            )
            task = self.task(task["id"])
            outcome = task["data"].get("review_outcome")
            if outcome and outcome.get("track") == task["data"]["track_id"]:
                return outcome
            session = service.store.get_session(session.id)
            if session.status != SessionStatus.CLOSED and session.wake_at > time.time():
                # Provider outage or an interrupted turn: the timer resumes
                # this session and its verdict continues the task.
                self.update(task, "reviewing", "The subtitle review paused and will continue automatically.")
                return None
            if session.outcome == CaseState.BUDGET_LIMITED:
                self.update(
                    task,
                    "review_pending",
                    "Subtitles ready. Checking stopped at the household AI spending limit; raise it in Defaults, then try again.",
                )
                return None
            if len(task["data"]["review"]["judgements"]) == done:
                break
            # Progress lives in the tools, so a fresh conversation continues
            # the review without editing an earlier one.
            session.status = SessionStatus.CLOSED
            session.closed_at = time.time()
            service.store.save_session(session)
        self.update(task, "review_pending", "Subtitles ready. The check didn't finish; try again.")
        return None

    def attach(self, service):
        service.toolbox.subtitles = self

        async def system(session):
            return pages_.SYSTEM

        async def load(ctx):
            task = self.task(ctx.session.download_id)
            if not task:
                raise ToolError("This subtitle review no longer exists.")
            self.authority(task)
            context = await self.review_context(task)
            if not context:
                raise ToolError("The dialogue evidence for this review is unavailable.")
            return task, context

        def view(context, page, glossed, ctx=None):
            state = context["state"]
            total = len(state["pages"])
            if glossed or context["same_language"]:
                if ctx is not None:
                    # Record that this conversation has now been shown the
                    # captions; judging must wait for a later turn.
                    state.setdefault("shown", {})[str(page["number"])] = [
                        ctx.session.id,
                        len(ctx.session.messages),
                    ]
                return pages_.revealed_view(
                    page,
                    total,
                    context["utterances"],
                    context["cues"],
                    state["glosses"].get(str(page["number"]), {}),
                    context["deltas"],
                    state["listens"],
                )
            return pages_.speech_view(page, total, context["utterances"], state["listens"])

        def find_page(context, number):
            page = next((p for p in context["state"]["pages"] if p["number"] == number), None)
            if not page:
                raise ToolError(f"There is no page {number}; pages run 1–{len(context['state']['pages'])}.")
            return page

        async def overview(ctx, args):
            task, context = await load(ctx)
            state, track = context["state"], context["track"]
            timing = track["data"].get("timing") or {}
            listing = []
            for page in state["pages"]:
                key = str(page["number"])
                listing.append(
                    {
                        "page": page["number"],
                        "from": pages_.ts(page["start"]),
                        "to": pages_.ts(page["end"]),
                        "speech_lines": len(pages_.page_utterances(page, context["utterances"])),
                        "captions": len(pages_.page_captions(page, context["cues"])),
                        "status": "judged" if key in state["judgements"] else "glossed" if key in state["glosses"] else "to do",
                    }
                )
            following = pages_.next_page(state, state["pages"])
            return {
                "track": {
                    "language": task["data"]["language"],
                    "style": task["data"]["kind"],
                    "source": track["data"].get("title") or track["data"]["source"],
                    "captions": len(context["cues"]),
                },
                "spoken_languages": context["evidence"]["coverage"].get("languages", {}),
                "captions_revealed_without_gloss": context["same_language"],
                "timing": {
                    "caption_start_vs_voice_seconds": timing.get("offset"),
                    "corrected_from": (timing.get("before") or {}).get("offset"),
                    "measured_captions": timing.get("paired_captions"),
                },
                "pages": listing,
                "progress": pages_.summary(state, state["pages"]),
                "next": (
                    f"Call page with page={following['number']}."
                    if following
                    else "Every page is judged; call verdict."
                ),
            }

        async def page(ctx, args):
            task, context = await load(ctx)
            found = find_page(context, args.get("page"))
            shown = view(context, found, str(found["number"]) in context["state"]["glosses"], ctx)
            self.review_state(task, context["state"])
            return shown

        async def gloss(ctx, args):
            task, context = await load(ctx)
            found = find_page(context, args.get("page"))
            try:
                glosses = pages_.check_gloss(found, context["utterances"], args.get("lines", []))
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            state = context["state"]
            state["glosses"][str(found["number"])] = glosses
            shown = view(context, found, True, ctx)
            self.review_state(task, state)
            return shown

        async def judge(ctx, args):
            task, context = await load(ctx)
            found = find_page(context, args.get("page"))
            state = context["state"]
            key = str(found["number"])
            if key not in state["glosses"] and not context["same_language"]:
                raise ToolError("Gloss this page's speech before judging its captions.")
            seen = state.get("shown", {}).get(key)
            if not seen or seen[0] != ctx.session.id or seen[1] >= len(ctx.session.messages):
                raise ToolError(
                    "Read this page's captions before judging them: call page for it, then judge in a later step."
                )
            try:
                judged, gaps = pages_.check_judgement(
                    found,
                    context["utterances"],
                    context["cues"],
                    state["listens"],
                    args.get("captions", []),
                    args.get("missing", []),
                )
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            state["judgements"][key] = judged
            state["missing"][key] = gaps
            total = len(state["pages"])
            self.review_state(
                task,
                state,
                f"Checking the captions against the dialogue ({len(state['judgements'])} of {total} pages).",
            )
            following = pages_.next_page(state, state["pages"])
            progress = pages_.summary(state, state["pages"])
            if not following:
                return f"Recorded page {key}. Every page is judged: {json.dumps(progress)}. Call verdict."
            shown = view(context, following, str(following["number"]) in state["glosses"], ctx)
            self.review_state(task, state)
            return f"Recorded page {key}. Progress: {json.dumps(progress)}.\n\n" + shown

        async def listen(ctx, args):
            task, context = await load(ctx)
            state = context["state"]
            start, end = float(args.get("start", -1)), float(args.get("end", -1))
            try:
                pages_.listen_allowance(state, start, end)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            track = context["track"]
            _, asset = self.authority(task)
            key = task["data"]["speech"]["path"].split("/")[1]
            try:
                heard = await self.nodes.execute(
                    track["node_id"],
                    "subtitle_listen",
                    {
                        "root_id": asset["root_id"],
                        "path": asset["path"],
                        "evidence": key,
                        "start": start,
                        "end": end,
                        "language": args.get("language") or None,
                        "model": args.get("model", "standard"),
                    },
                    timeout=660,
                )
            except NodeError as exc:
                raise ToolError(str(exc)) from exc
            self.authority(task)
            heard["id"] = f"L{len(state['listens']) + 1}"
            state["listens"].append(heard)
            self.review_state(task, state)
            lines = [
                f"{pages_.ts(pages_.speech_start(u))} {heard['id']}:{n} {u['text']}{pages_.marks(u)}"
                for n, u in enumerate(heard["utterances"])
            ]
            return (
                f"Re-listen {heard['id']} ({heard['model']}, {heard['language']}) "
                f"{pages_.ts(start)}–{pages_.ts(end)}; cite these IDs like speech lines:\n"
                + ("\n".join(lines) or "(no speech recognised)")
            )

        async def verdict(ctx, args):
            task, context = await load(ctx)
            approved = args.get("approved") is True
            reason = str(args.get("reason", "")).strip()
            if not 1 <= len(reason) <= 1000:
                raise ToolError("Give a short plain reason for the verdict.")
            state = context["state"]
            if approved:
                reasons = pages_.gate(
                    state,
                    state["pages"],
                    context["cues"],
                    context["utterances"],
                    task["data"]["kind"],
                    context["measured"],
                )
                if reasons:
                    raise ToolError("Approval refused: " + " ".join(reasons))
            outcome = {
                "track": context["track"]["id"],
                "approved": approved,
                "reason": reason,
                "summary": pages_.summary(state, state["pages"]),
                "session_id": ctx.session.id,
                "model": ctx.session.model,
                "reviewed_at": time.time(),
            }
            self.save_track(context["track"]["id"], review=outcome)
            self.update(task, task["state"], task["data"].get("message", ""), review_outcome=outcome)
            ctx.close = True
            ctx.close_reason = reason
            self.launch(task["id"])  # Continues the task if no run is waiting.
            return {"recorded": True, "approved": approved}

        obj = lambda properties, required: {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        speech_ids = {"type": "array", "items": {"type": "string"}}
        tools = [
            ToolDef(
                "overview",
                "Track, timing measurement, pages and progress. Call first.",
                obj({}, []),
                overview,
            ),
            ToolDef(
                "page",
                "Show one page: recognised speech only until you gloss it, then speech with your readings and the captions.",
                obj({"page": {"type": "integer", "minimum": 1}}, ["page"]),
                page,
            ),
            ToolDef(
                "gloss",
                "Record a brief English reading of every recognised speech line on a page; returns the page with its captions revealed.",
                obj(
                    {
                        "page": {"type": "integer", "minimum": 1},
                        "lines": {
                            "type": "array",
                            "items": obj(
                                {"speech": {"type": "string"}, "english": {"type": "string"}},
                                ["speech", "english"],
                            ),
                        },
                    },
                    ["page", "lines"],
                ),
                gloss,
            ),
            ToolDef(
                "judge",
                "Record a verdict for every caption on a glossed page, plus uncaptioned dialogue. Returns the next page.",
                obj(
                    {
                        "page": {"type": "integer", "minimum": 1},
                        "captions": {
                            "type": "array",
                            "items": obj(
                                {
                                    "caption": {"type": "string"},
                                    "verdict": {"type": "string", "enum": list(pages_.VERDICTS)},
                                    "speech": speech_ids,
                                    "note": {"type": "string"},
                                },
                                ["caption", "verdict", "speech"],
                            ),
                        },
                        "missing": {
                            "type": "array",
                            "items": obj(
                                {"speech": speech_ids, "note": {"type": "string"}},
                                ["speech"],
                            ),
                        },
                    },
                    ["page", "captions", "missing"],
                ),
                judge,
            ),
            ToolDef(
                "listen",
                "Re-recognise up to 60 seconds of the soundtrack with another language hint or the larger model. Adds evidence; nothing is replaced.",
                obj(
                    {
                        "start": {"type": "number", "minimum": 0},
                        "end": {"type": "number", "minimum": 0},
                        "language": {"type": "string"},
                        "model": {"type": "string", "enum": ["standard", "large"]},
                    },
                    ["start", "end"],
                ),
                listen,
            ),
            ToolDef(
                "verdict",
                "Approve or reject the track once every page is judged. Approval is refused unless the judged evidence supports it.",
                obj(
                    {"approved": {"type": "boolean"}, "reason": {"type": "string"}},
                    ["approved", "reason"],
                ),
                verdict,
            ),
        ]
        service.runtime.register(
            AgentSpec(
                AgentKind.SUBTITLE.value,
                review_model,
                system,
                lambda _: tools,
                max_steps=40,
                max_tokens=16000,
                cache=True,
                effort=os.getenv("SPARROW_SUBTITLE_EFFORT", "medium"),
            )
        )

    # ─── The task ─────────────────────────────────────────────────────────

    def set_aside(self, task, track, reason, *, fallback=False):
        attempts = [
            {**a, "outcome": "set_aside", "reason": reason}
            if a.get("track_id") in (track["id"], track["data"].get("replaces"))
            or a.get("source_id") == track["data"].get("source_id")
            else a
            for a in task["data"].get("attempts", [])
        ]
        values = {"attempts": attempts, "track_id": None, "review": None, "review_outcome": None}
        if fallback and not task["data"].get("fallback"):
            values["fallback"] = track["id"]  # Still playable if nothing better turns up.
        else:
            self.save_track(track["id"], state="rejected")
        self.update(task, "finding", "Trying another subtitle source.", **values)

    def finish(self, task, track_id, *, checked=False, note=""):
        track = self.track(track_id)
        data = track["data"]
        with self.accounts.connect() as db:
            rows = db.execute(
                "SELECT id,data FROM subtitle_tracks WHERE asset_id=? AND state='ready' AND id!=?",
                (task["asset_id"], track_id),
            ).fetchall()
            for row in rows:
                other = json.loads(row["data"])
                same = other.get("source_id") == data.get("source_id") and other.get(
                    "audio_index"
                ) == data.get("audio_index")
                # Another person's checked copy is never replaced by an unchecked one.
                keep = bool((other.get("review") or {}).get("approved")) and not checked
                if (same and not keep) or row["id"] == task["data"].get("fallback"):
                    db.execute(
                        "UPDATE subtitle_tracks SET state='superseded' WHERE id=?",
                        (row["id"],),
                    )
        timing = data.get("timing") or {}
        if checked:
            message = "Subtitles checked against the dialogue."
        elif timing.get("within_tolerance") and data.get("correction"):
            message = "Subtitles ready. Their timing was adjusted to follow the voice."
        elif timing.get("within_tolerance"):
            message = "Subtitles ready and in time with the voice."
        else:
            message = "Subtitles ready."
        if data.get("correction") and checked:
            message += " Their timing was adjusted to follow the voice."
        self.update(
            task,
            "ready",
            (message + " " + note).strip(),
            code="subtitle_checked" if checked else "subtitle_ready",
            track_id=track_id,
        )

    async def run(self, identity):
        task = self.task(identity)
        if not task:
            return
        try:
            for _ in range(3 * MAX_CANDIDATES + 4):
                task = self.task(identity)
                user, asset = self.authority(task)
                data = task["data"]
                track = self.track(data.get("track_id"))
                if not track or track["state"] != "ready":
                    if await self.find(task, user, asset):
                        continue
                    fallback = self.track(data.get("fallback"))
                    if fallback and fallback["state"] == "ready":
                        self.finish(task, fallback["id"], note="Their timing could not be matched to the dialogue.")
                        await self.notify_ready(task)
                        return
                    reason = next(
                        (a["reason"] for a in reversed(data.get("attempts", [])) if a.get("reason")),
                        "No matching subtitle was found. Add a subtitle file or configure a provider.",
                    )
                    self.update(task, "needs_attention", reason)
                    return
                if self.wants_analysis(task, asset) and data.get("measured") != track["id"]:
                    outcome = await self.measure(task, asset, track)
                    if outcome == "inconsistent":
                        self.set_aside(
                            task,
                            track,
                            "Its timing does not follow this title's dialogue.",
                            fallback=not data.get("verify"),
                        )
                    continue
                if data.get("verify"):
                    outcome = data.get("review_outcome")
                    if not outcome or outcome.get("track") != track["id"]:
                        outcome = await self.review(task)
                    if outcome is None:
                        return
                    if outcome["approved"] is None:
                        self.finish(task, track["id"], note="They could not be checked: " + outcome["reason"])
                        await self.notify_ready(self.task(identity))
                        return
                    if not outcome["approved"]:
                        self.set_aside(self.task(identity), track, outcome["reason"])
                        continue
                    self.finish(self.task(identity), track["id"], checked=True)
                else:
                    self.finish(task, track["id"])
                await self.notify_ready(self.task(identity))
                return
            self.update(self.task(identity), "needs_attention", "Subtitle work stopped after too many attempts.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            current = self.task(identity)
            if current and current["state"] != "cancelled":
                self.update(
                    current,
                    "needs_attention",
                    str(exc)[:1500] or "Couldn’t prepare the subtitles. Try again.",
                )


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Repair(Input):
    language: str | None = None
    kind: str | None = None
    audio_index: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, max_length=2 * 1024 * 1024)
    format: str = "srt"
    verify: bool | None = None
    repair: bool = False


class Offset(Input):
    seconds: float = Field(ge=-30, le=30, allow_inf_nan=False)


class ProviderSettings(Input):
    api_key: str = Field(default="", max_length=500)
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=500)
    clear: bool = False


def install_subtitles(app, accounts, nodes, catalogue, get_service):
    subtitles = Subtitles(accounts, nodes, catalogue, get_service)
    app.state.subtitles = subtitles
    router = APIRouter(prefix="/api/v1")

    def task_for(request, identity):
        task = subtitles.task(identity)
        if not task or task["user_id"] != request.state.user["id"]:
            raise HTTPException(404, "Subtitle request not found.")
        return task

    @router.get("/assets/{asset_id}/subtitles")
    def tracks(asset_id: str, request: Request):
        if not catalogue.asset(request.state.user, asset_id):
            raise HTTPException(404, "Media not found.")
        with accounts.connect() as db:
            rows = db.execute(
                "SELECT id FROM subtitle_tasks WHERE asset_id=? AND user_id=? ORDER BY updated DESC LIMIT 3",
                (asset_id, request.state.user["id"]),
            ).fetchall()
        return {
            "tracks": subtitles.tracks(request.state.user, asset_id),
            "preferences": accounts.resolve(request.state.user["id"]),
            "tasks": [
                {
                    "id": t["id"],
                    "state": t["state"],
                    "message": t["data"].get("message", ""),
                    "track_id": t["data"].get("track_id"),
                }
                for t in (subtitles.task(r["id"]) for r in rows)
            ],
        }

    @router.post("/assets/{asset_id}/subtitles/repair")
    async def repair(asset_id: str, body: Repair, request: Request):
        try:
            upload = (
                {"text": body.text, "format": body.format}
                if body.text is not None
                else None
            )
            return await subtitles.enqueue(
                request.state.user,
                asset_id,
                upload=upload,
                language=body.language,
                kind=body.kind,
                audio_index=body.audio_index,
                verify=body.verify,
                repair=body.repair,
            )
        except (ValueError, ToolError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/subtitles/tasks/{identity}")
    def status(identity: str, request: Request):
        task = task_for(request, identity)
        return {
            "id": identity,
            "state": task["state"],
            "message": task["data"].get("message", ""),
            "attempts": task["data"].get("attempts", []),
        }

    @router.post("/subtitles/tasks/{identity}/review")
    async def review(identity: str, request: Request):
        task = task_for(request, identity)
        if task["state"] != "review_pending":
            raise HTTPException(409, "This subtitle is not waiting for review.")
        subtitles.update(task, "reviewing", "Checking the captions against the dialogue.")
        subtitles.launch(identity)
        return {"ok": True}

    @router.delete("/subtitles/tasks/{identity}")
    async def cancel(identity: str, request: Request):
        task = task_for(request, identity)
        subtitles.update(
            task, "cancelled", "Subtitle work stopped. Existing tracks are preserved."
        )
        service = get_service()
        for session_id in task["data"].get("review_sessions", []):
            session = service.store.get_session(session_id) if service else None
            if session and session.status != SessionStatus.CLOSED:
                session.status = SessionStatus.CLOSED
                session.wake_at = 0
                service.store.save_session(session)
        running = subtitles.tasks.get(identity)
        if running:
            running.cancel()
        return {"ok": True}

    @router.patch("/subtitles/tracks/{identity}/offset")
    def offset(identity: str, body: Offset, request: Request):
        with accounts.connect() as db:
            row = db.execute(
                "SELECT asset_id FROM subtitle_tracks WHERE id=?", (identity,)
            ).fetchone()
        if not row or not catalogue.asset(request.state.user, row["asset_id"]):
            raise HTTPException(404, "Subtitle track not found.")
        with accounts.connect() as db:
            db.execute(
                "INSERT INTO subtitle_offsets VALUES(?,?,?) ON CONFLICT(user_id,track_id) DO UPDATE SET seconds=excluded.seconds",
                (request.state.user["id"], identity, body.seconds),
            )
        return {"seconds": body.seconds}

    @router.get("/subtitles/tracks/{identity}.vtt")
    async def caption(identity: str, request: Request, original: bool = False):
        with accounts.connect() as db:
            row = db.execute(
                "SELECT * FROM subtitle_tracks WHERE id=?", (identity,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Subtitle track not found.")
        asset = catalogue.asset(request.state.user, row["asset_id"])
        data = json.loads(row["data"])
        if (
            not asset
            or data["media_version"] != asset["facts"]["version"]
            or (row["state"] != "ready" and not original)
        ):
            raise HTTPException(404, "This subtitle is not ready for this media copy.")
        if original:
            if not data.get("original_version"):
                raise HTTPException(404, "The original track is unavailable.")
            data = {
                **data,
                "path": data["original_path"],
                "version": data["original_version"],
            }
        try:
            raw = (
                await subtitles.read(row["node_id"], "cache", data["path"], data["version"])
            ).decode("utf8")
        except (NodeError, UnicodeError, ValueError) as exc:
            raise HTTPException(
                503,
                "The subtitle’s storage is unavailable. Reconnect it and try again.",
            ) from exc
        from .subtitle_worker import cues_from_text, render

        with accounts.connect() as db:
            personal = db.execute(
                "SELECT seconds FROM subtitle_offsets WHERE user_id=? AND track_id=?",
                (request.state.user["id"], identity),
            ).fetchone()
        if personal and personal["seconds"] and not original:
            cues = cues_from_text(raw, "vtt")
            delta = personal["seconds"]
            raw = render(
                [
                    {
                        **c,
                        "start": max(0, c["start"] + delta),
                        "end": max(0.001, c["end"] + delta),
                    }
                    for c in cues
                    if c["end"] + delta > 0
                ],
                vtt=True,
            )
        return Response(
            raw, media_type="text/vtt", headers={"Cache-Control": "private,no-store"}
        )

    @router.get("/admin/subtitles/provider")
    def provider(request: Request):
        administrator(request)
        data = subtitles.provider()
        return {
            "configured": bool(data.get("api_key")),
            "account_configured": bool(data.get("username") and data.get("password")),
        }

    @router.put("/admin/subtitles/provider")
    def configure(body: ProviderSettings, request: Request):
        administrator(request)
        current = {} if body.clear else subtitles.provider()
        current.update(
            {k: v for k, v in body.model_dump(exclude={"clear"}).items() if v}
        )
        with accounts.connect() as db:
            db.execute(
                "UPDATE subtitle_provider SET data=? WHERE id=1", (canonical(current),)
            )
        return {"configured": bool(current.get("api_key"))}

    app.include_router(router)
    return subtitles
