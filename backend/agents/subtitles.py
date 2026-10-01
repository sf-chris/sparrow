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

from . import openai_loop, subtitle_contract
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
DEFAULT_OPENAI_REVIEW_MODEL = "gpt-6-sol"
DEFAULT_CONTRACTOR = "gpt-6-luna"
DEFAULT_VERIFIER = "gpt-6-sol"
# Above this share of captions flagged wrong, the track is plainly mismatched:
# skip verification and let the manager replace it.
MISMATCH_SHARE = 0.3
AUDIT_PAGES = 2
AUDIT_SAMPLE = 10  # One in this many trusted tracks still gets blind audits.
TRUSTED_SOURCES = ("embedded:", "sidecar:")
QUEUE_LIMIT = 500
MAX_CANDIDATES = 4
EVIDENCE_SLICE = 540
REVIEW_WAKES = 6


def review_model():
    """The manager. GPT-6-Sol when an OpenAI key is set: it made the same
    corrections as Opus on the benchmark episodes for about a third of the
    cost. Otherwise Opus."""
    configured = os.getenv("SPARROW_SUBTITLE_MODEL")
    if configured:
        return configured
    return DEFAULT_OPENAI_REVIEW_MODEL if os.getenv("OPENAI_API_KEY") else DEFAULT_REVIEW_MODEL


def verifier_model():
    """A stronger cheap model that re-checks only the checker's flags.

    In the benchmark GPT-6-Sol cleared every false alarm on correct tracks
    while keeping wrong-episode tracks flagged, for about two cents an episode.
    """
    return configured_model("SPARROW_SUBTITLE_VERIFIER", DEFAULT_VERIFIER)


def contractor_model():
    """The cheaper page checker the subtitle agent manages.

    GPT-6-Luna caught 6 of Opus's 7 flagged problems in the page-check
    benchmark for under a cent per episode, so it is used whenever an OpenAI
    key is present. Empty means Opus reads every page itself.
    """
    return configured_model("SPARROW_SUBTITLE_CONTRACTOR", DEFAULT_CONTRACTOR)


def configured_model(variable, default):
    """A named model, "off", or blank for the default when an OpenAI key is set."""
    configured = (os.getenv(variable) or "").strip()
    if configured.lower() == "off":
        return ""
    return configured or (default if os.getenv("OPENAI_API_KEY") else "")


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
        # Builds the page checker's model caller; tests and benchmarks replace it.
        self.contract_caller = lambda model: subtitle_contract.caller_for(
            model,
            self.get_service().runtime._api_key_getter(),
            os.getenv("OPENAI_API_KEY", ""),
            os.getenv("SPARROW_SUBTITLE_CONTRACTOR_EFFORT", ""),
        )
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
        uploaded = (
            [
                {
                    "id": "upload",
                    "source": "upload",
                    "title": "Your subtitle file",
                    "language": language,
                    "kind": kind,
                }
            ]
            if values.get("upload")
            else []
        )
        listing = await self.nodes.execute(
            asset["node_id"],
            "subtitle_candidates",
            {"root_id": asset["root_id"], "path": asset["path"]},
            timeout=300,
        )
        self.authority(task)
        self.update(task, task["state"], task["data"].get("message", ""), movie_hash=listing.get("movie_hash"))
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
        if uploaded:
            # The person's file is tried first; the file's own tracks stay
            # available to the subtitle agent if the upload is wrong.
            return uploaded + local, audio
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
                    if candidate["source"] == "upload":
                        # Say what is wrong with the person's own file rather
                        # than quietly substituting another track.
                        self.update(
                            task,
                            "finding",
                            "Your subtitle file could not be used.",
                            attempts=attempts,
                            upload_failed=f"Your subtitle file could not be used: {exc}"[:500],
                        )
                        return False
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

    async def measured(self, node, track, evidence, info):
        cues = await self.cues(node, track["data"]["path"], track["data"]["version"])
        key = (track["id"], info["version"]["size_bytes"], info["version"].get("mtime_ns"))
        if key not in self._measures:
            recent = list(self._measures.items())[-8:]
            self._measures = {**dict(recent), key: estimate(cues, evidence)}
        return cues, self._measures[key]

    async def review_context(self, task, asset):
        info = task["data"].get("speech")
        if not info:
            return None
        node = asset["node_id"]
        evidence = await self.speech(node, info)
        utterances = evidence["utterances"]
        duration = evidence["source"]["audio_seconds"]
        track = self.track(task["data"].get("track_id"))
        if track and track["state"] == "ready":
            cues, measured = await self.measured(node, track, evidence, info)
        else:
            track, cues = None, []
            measured = {"measurable": False, "reason": "No subtitle track is in use."}
        state = task["data"].get("review")
        if not state:
            state = {
                "track": track and track["id"],
                "pages": pages_.build_pages(utterances, cues, duration),
                "glosses": {},
                "judgements": {},
                "missing": {},
                "shown": {},
                "listens": [],
                "written": {},
                "actions": [],
            }
        languages = evidence["coverage"].get("languages", {})
        spoken = max(languages, key=languages.get) if languages else ""
        return {
            "task": task,
            "asset": asset,
            "node": node,
            "info": info,
            "track": track,
            "evidence": evidence,
            "cues": cues,
            "measured": measured,
            "deltas": {p["cue"]: p["delta"] for p in measured.get("pairs", [])},
            "utterances": utterances,
            "duration": duration,
            "state": state,
            "same_language": language_code(spoken) == task["data"]["language"],
        }

    def review_state(self, task, state, message=None):
        self.update(task, "reviewing", message or task["data"].get("message", ""), review=state)

    async def activate(self, context, track_id, action, *, rebuild, renamed=None):
        """Make a track the one under review, re-measured, keeping what still holds."""
        task, state = context["task"], context["state"]
        track = self.track(track_id)
        cues, measured = await self.measured(context["node"], track, context["evidence"], context["info"])
        self.save_track(
            track_id,
            timing={**timing_summary(measured), "within_tolerance": in_window(measured)},
        )
        shown = {}
        if rebuild:
            pages = pages_.build_pages(context["utterances"], cues, context["duration"])
            judgements, missing, reopen = {}, {}, []
        else:
            pages = state["pages"]
            judgements, missing, reopen = pages_.carry(
                pages, context["cues"], pages, cues, state, renamed
            )
            # A page stays read when its caption IDs still name the same words
            # (a retime or an edit elsewhere); otherwise it must be read again.
            same = {
                str(page["number"])
                for page in pages
                if [(i, c["text"]) for i, c in pages_.page_captions(page, context["cues"])]
                == [(i, c["text"]) for i, c in pages_.page_captions(page, cues)]
            }
            shown = {k: v for k, v in state.get("shown", {}).items() if k in same}
        state.update(
            track=track_id, pages=pages, judgements=judgements, missing=missing, shown=shown
        )
        state["actions"] = (state.get("actions") or [])[-40:] + [
            {"action": action, "track": track_id, "at": time.time()}
        ]
        self.update(
            task,
            "reviewing",
            "Correcting the subtitles against the dialogue.",
            track_id=track_id,
            measured=track_id,
            review=state,
        )
        context.update(cues=cues, measured=measured, deltas={p["cue"]: p["delta"] for p in measured.get("pairs", [])})
        return measured, pages, reopen

    async def derive(self, context, cues, detail):
        """A new copy of the track under review with changed captions."""
        from .subtitle_worker import render

        base, task, asset = context["track"], context["task"], context["asset"]
        text = render(cues, vtt=True)
        candidate = {
            "id": base["data"]["source_id"],
            "source": base["data"]["source"],
            "title": base["data"].get("title", ""),
        }
        new_id = await self.prepare(
            task,
            asset,
            candidate,
            text,
            "vtt",
            base["data"]["audio_index"],
            suffix=":" + hashlib.sha256(text.encode()).hexdigest()[:16],
        )
        self.save_track(
            new_id,
            original_path=base["data"]["original_path"],
            original_version=base["data"]["original_version"],
            derived_from=base["id"],
            edits=(base["data"].get("edits") or [])[-20:] + [detail],
            correction=base["data"].get("correction") or {"kind": "edited"},
        )
        if new_id != base["id"]:
            self.save_track(base["id"], state="superseded")
        return new_id

    async def contract(self, context, *, keep_manager=False):
        """Have the page checker translate and judge every page of the track in use.

        With no track, its translations become a written draft for the manager
        to review. Manager verdicts survive when asked (after a source switch
        they do not, because the captions changed).
        """
        task, state = context["task"], context["state"]
        model = contractor_model()
        caller = self.contract_caller(model)
        if context["track"] is None:
            found = await subtitle_contract.check_pages(
                caller, state["pages"], context["utterances"], [], {}, self.spoken(context), state.get("translations"), compare=False
            )
            state["translations"] = found["translations"]
            state["written"] = subtitle_contract.draft_from_translations(
                state["pages"], context["utterances"], found["translations"]
            )
            state["contract"] = {"model": model, "track": "none", "spend": found["spend"], "failures": found["spend"]["failures"], "hints": found["hints"]}
            self.charge_checkers(task, state, [found["spend"]])
            self.review_state(task, state)
            if state["written"]:
                await self.use_written_draft(context)
            return
        found = await subtitle_contract.check_pages(
            caller,
            state["pages"],
            context["utterances"],
            context["cues"],
            context["deltas"],
            self.spoken(context),
            state.get("translations"),
        )
        state["translations"] = found["translations"]
        verified = None
        wrong = sum(e["verdict"] == "wrong" for page in found["verdicts"].values() for e in page.values())
        if verifier_model() and wrong <= MISMATCH_SHARE * max(1, len(context["cues"])):
            verified = await subtitle_contract.verify_flags(
                self.contract_caller(verifier_model()),
                state["pages"],
                context["utterances"],
                context["cues"],
                found["translations"],
                context["deltas"],
                found["verdicts"],
                found["missing"],
            )
        judgements = {}
        for key, record in found["verdicts"].items():
            kept = {k: v for k, v in state.get("judgements", {}).get(key, {}).items() if keep_manager and v.get("by") == "manager"}
            judgements[key] = {**record, **kept}
        state["judgements"] = judgements
        state["missing"] = found["missing"]
        spend = state.get("contract", {}).get("spend", {})
        state["contract"] = {
            "model": model,
            "track": context["track"]["id"],
            "verifier": verified and verified["model"],
            "mismatched": wrong > MISMATCH_SHARE * max(1, len(context["cues"])),
            "spend": {
                "calls": spend.get("calls", 0) + found["spend"]["calls"] + (verified or {}).get("calls", 0),
                "dollars": round(
                    spend.get("dollars", 0) + found["spend"]["dollars"] + (verified or {}).get("dollars", 0), 4
                ),
            },
            "failures": found["spend"]["failures"] + (verified or {}).get("failures", []),
            "hints": found["hints"],
            "snapshot": pages_.contractor_snapshot({"judgements": judgements}),
        }
        audit = state.get("audit") or {"pages": [], "done": [], "misses": []}
        if not audit["pages"] and self.audit_due(task, context):
            audit["pages"] = pages_.choose_audits(
                state["pages"], context["cues"], context["utterances"], task["id"], AUDIT_PAGES
            )
        state["audit"] = audit
        self.charge_checkers(task, state, [found["spend"], verified or {}])
        self.review_state(task, state)

    def audit_due(self, task, context):
        """Blind audits keep the page checker honest: always for tracks from
        uploads, providers or Sparrow itself, and for a steady sample of the
        file's own tracks."""
        source = ((context["track"] or {}).get("data") or {}).get("source_id", "")
        sampled = int(hashlib.sha256(task["id"].encode()).hexdigest(), 16) % AUDIT_SAMPLE == 0
        return sampled or not source.startswith(TRUSTED_SOURCES)

    async def free_fixes(self, task, asset, context):
        """Predictable corrections before any model reads the track: OCR
        confusions such as "l'm" and sections outside the timing window.
        Returns the refreshed context."""
        ocr = pages_.detect(context["cues"], context["utterances"]).get("ocr", [])
        if ocr:
            cues, _, renamed = pages_.edit(
                context["cues"],
                context["utterances"],
                context["state"].get("listens", []),
                [],
                [],
                [{"find": o["find"], "with": o["with"]} for o in ocr],
            )
            if renamed:
                new_id = await self.derive(context, cues, {"kind": "edit", "replaced": len(renamed), "automatic": "ocr"})
                await self.activate(context, new_id, "fix scanning errors", rebuild=False, renamed=renamed)
                context = await self.review_context(self.task(task["id"]), asset)
        measured = context["measured"]
        if measured.get("consistent") and pages_.off_sections(measured):
            cues, detail = pages_.retime(context["cues"], measured, "sections", None)
            new_id = await self.derive(context, cues, {"kind": "retime", "detail": detail, "automatic": "sections"})
            await self.activate(context, new_id, "retime " + detail, rebuild=False)
            context = await self.review_context(self.task(task["id"]), asset)
        return context

    def checker_outcome(self, task, context):
        """The cascade: settle the track without the manager when the checkers can.

        A track the checker finds mostly wrong is rejected (the next source is
        tried); a track with nothing flagged, no missing dialogue and timing in
        the window is approved unless an audit is due. Anything else goes to
        the manager."""
        state, track = context["state"], context["track"]
        if not track or not state.get("contract"):
            return None
        if state["contract"].get("mismatched"):
            outcome = {"approved": False, "reason": "Most of these subtitles don't match the dialogue."}
        elif not (state.get("audit") or {}).get("pages") and not pages_.manager_gate(
            state, state["pages"], context["cues"], context["utterances"], task["data"]["kind"], context["measured"]
        ):
            outcome = {"approved": True, "reason": "The page checker found nothing to correct."}
        else:
            return None
        outcome.update(
            track=track["id"],
            summary=pages_.summary(state, state["pages"]),
            model=state["contract"].get("model"),
            reviewed_at=time.time(),
        )
        self.save_track(track["id"], review=outcome)
        self.update(task, task["state"], task["data"].get("message", ""), review_outcome=outcome)
        return outcome

    def charge_checkers(self, task, state, spends):
        """Record page-checker spend against this title's allowance.

        A closed ledger session in the review's budget scope carries it, so
        one allowance (and the household's accounting) covers the checkers
        and the manager together.
        """
        service = self.get_service()
        dollars = sum(s.get("dollars", 0) for s in spends)
        if not service or dollars <= 0:
            return
        ledger = state.get("checker_session") and service.store.get_session(state["checker_session"])
        if not ledger:
            ledger = AgentSession(
                agent=AgentKind.SUBTITLE,
                user_id=task["user_id"],
                download_id=task["id"],
                model=contractor_model(),
                job_id=task["data"].get("job_id", ""),
                job_revision=task["data"].get("job_revision") or 1,
                budget_scope="subtitle:" + task["id"],
            )
            ledger.status = SessionStatus.CLOSED
            ledger.closed_at = time.time()
        for spend in spends:
            if spend.get("dollars"):
                ledger.spend.turns += spend.get("calls", 0)
                ledger.spend.dollars = round(ledger.spend.dollars + spend["dollars"], 6)
                ledger.spend.entries.append({"model": spend.get("model", ""), "calls": spend.get("calls", 0), "dollars": spend["dollars"]})
        service.store.save_session(ledger)
        state["checker_session"] = ledger.id

    def spoken(self, context):
        languages = context["evidence"]["coverage"].get("languages", {})
        return max(languages, key=languages.get) if languages else ""

    async def use_written_draft(self, context):
        """Put the checker's translated draft in use for the manager to review."""
        from .subtitle_worker import render

        task, state = context["task"], context["state"]
        cues = pages_.compose(state["pages"], state["written"], context["utterances"], state["listens"])
        candidate = {"id": "written", "source": "written", "title": "Written by Sparrow from the dialogue"}
        new_id = await self.prepare(
            task,
            context["asset"],
            candidate,
            render(cues, vtt=True),
            "vtt",
            task["data"]["audio_index"],
            suffix=":" + hashlib.sha256(json.dumps(state["written"], sort_keys=True).encode()).hexdigest()[:16],
        )
        measured, pages, _ = await self.activate(context, new_id, "draft from page translations", rebuild=True)
        state = self.task(task["id"])["data"]["review"]
        track = self.track(new_id)
        new_cues = await self.cues(context["node"], track["data"]["path"], track["data"]["version"])
        for page in pages:
            state["judgements"][str(page["number"])] = {
                pages_.caption_id(i): {"verdict": "ok", "speech": cues[i]["speech"], "note": "checker's translation", "by": "contractor draft"}
                for i, _ in pages_.page_captions(page, new_cues)
                if i < len(cues)
            }
        gaps = subtitle_contract.untranslated(pages, context["utterances"], state.get("translations", {}))
        for page in pages:
            state["missing"][str(page["number"])] = gaps.get(str(page["number"]), [])
        state["contract"]["track"] = new_id
        state["audit"] = {
            "pages": pages_.choose_audits(pages, new_cues, context["utterances"], task["id"], AUDIT_PAGES),
            "done": [],
            "misses": [],
        }
        self.review_state(self.task(task["id"]), state, "Checking the written subtitles.")

    async def review(self, task):
        """Run the subtitle agent until it records a verdict; None while pending."""
        service = self.get_service()
        openai = openai_loop.is_openai_model(review_model())
        if not service or not (service.runtime._openai_key_getter() if openai else service.runtime._api_key_getter()):
            self.update(
                task,
                "review_pending",
                f"Subtitles ready. Checking them needs an {'OpenAI' if openai else 'Anthropic'} key. Add one, then try again.",
            )
            return None
        _, asset = self.authority(task)
        context = await self.review_context(task, asset)
        if not context:
            return {"approved": False, "track": task["data"].get("track_id"), "reason": "Dialogue analysis is unavailable for this media."}
        if contractor_model() and context["state"].get("contract", {}).get("track") != (context["track"] or {}).get("id", "none"):
            if context["track"]:
                context = await self.free_fixes(task, asset, context)
            self.update(self.task(task["id"]), "reviewing", "Checking each page of the subtitles against the dialogue.")
            await self.contract(context)
            task = self.task(task["id"])
            context = await self.review_context(task, asset)
            outcome = self.checker_outcome(task, context)
            if outcome:
                return outcome
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
            state = task["data"]["review"]
            before = (len(state["judgements"]), len(state.get("actions", [])), len(state.get("written", {})))
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
                f"Checking the subtitles against the dialogue ({before[0]} of {len(state['pages'])} pages).",
                review_session=session.id,
                review_sessions=sessions,
            )
            await service.runtime.wake(
                session.id,
                Event(
                    kind="subtitle_review",
                    payload={
                        "description": (
                            "Make this title's subtitles right. Start with overview."
                            if not any(before)
                            else "Continue the subtitle work. Call overview to see what remains."
                        )
                    },
                ),
            )
            task = self.task(task["id"])
            outcome = task["data"].get("review_outcome")
            if outcome:
                return outcome
            session = service.store.get_session(session.id)
            if session.status != SessionStatus.CLOSED and session.wake_at > time.time():
                # Provider outage or an interrupted turn: the timer resumes
                # this session and its verdict continues the task.
                self.update(task, "reviewing", "The subtitle work paused and will continue automatically.")
                return None
            if session.outcome == CaseState.BUDGET_LIMITED:
                self.update(
                    task,
                    "review_pending",
                    "Subtitles ready. Checking stopped at its spending limit; raise it in Defaults, then try again.",
                )
                return None
            state = task["data"]["review"]
            after = (len(state["judgements"]), len(state.get("actions", [])), len(state.get("written", {})))
            if after == before:
                break
            # Progress lives in the tools, so a fresh conversation continues
            # the work without editing an earlier one.
            session.status = SessionStatus.CLOSED
            session.closed_at = time.time()
            service.store.save_session(session)
        self.update(task, "review_pending", "Subtitles ready. The check didn't finish; try again.")
        return None

    def attach(self, service):
        service.toolbox.subtitles = self

        def managing(session):
            task = self.task(session.download_id)
            return bool(task and ((task["data"].get("review") or {}).get("contract")))

        async def system(session):
            return pages_.MANAGER_SYSTEM if managing(session) else pages_.SYSTEM

        async def load(ctx):
            task = self.task(ctx.session.download_id)
            if not task:
                raise ToolError("This subtitle work no longer exists.")
            _, asset = self.authority(task)
            context = await self.review_context(task, asset)
            if not context:
                raise ToolError("The dialogue evidence for this title is unavailable.")
            return task, context

        def remember_shown(ctx, state, page):
            # Judging must wait for a later turn than the one that revealed
            # the captions, so a reviewer cannot judge captions it never read.
            state.setdefault("shown", {})[str(page["number"])] = [ctx.session.id, len(ctx.session.messages)]

        def mark_seen(ctx, state, captions):
            seen = state.setdefault("seen", {})
            for caption in captions:
                seen[caption] = [ctx.session.id, len(ctx.session.messages)]

        def view(ctx, context, page):
            state = context["state"]
            total = len(state["pages"])
            audited = page["number"] in (state.get("audit") or {}).get("pages", [])
            if state.get("contract") and not audited:
                mark_seen(ctx, state, [pages_.caption_id(i) for i, _ in pages_.page_captions(page, context["cues"])])
                return pages_.inspect_view(
                    page, total, context["utterances"], context["cues"], state.get("translations", {}),
                    state["judgements"], context["deltas"], state["listens"],
                )
            if context["same_language"] or pages_.glossed(page, context["utterances"], state["glosses"]):
                mark_seen(ctx, state, [pages_.caption_id(i) for i, _ in pages_.page_captions(page, context["cues"])])
                remember_shown(ctx, state, page)
                return pages_.revealed_view(
                    page, total, context["utterances"], context["cues"], state["glosses"], context["deltas"], state["listens"]
                )
            return pages_.speech_view(page, total, context["utterances"], state["listens"])

        def find_page(context, number):
            page = next((p for p in context["state"]["pages"] if p["number"] == number), None)
            if not page:
                raise ToolError(f"There is no page {number}; pages run 1–{len(context['state']['pages'])}.")
            return page

        def sources_list(task, context):
            attempts = {a["source_id"]: a for a in task["data"].get("attempts", [])}
            current = (context["track"] or {}).get("data", {}).get("source_id")
            out = []
            for candidate in task["data"].get("candidates", []):
                tried = attempts.get(candidate["id"])
                out.append(
                    {
                        "id": candidate["id"],
                        "kind": candidate["source"],
                        "title": candidate.get("title", "")[:80],
                        "language": candidate.get("language", ""),
                        "style": candidate.get("kind", ""),
                        "captions": candidate.get("cue_count") or None,
                        "matches_this_file": candidate.get("hash_match"),
                        "status": "in use" if candidate["id"] == current else (tried or {}).get("outcome", "untried"),
                        "note": (tried or {}).get("reason", "")[:160],
                    }
                )
            return out

        async def overview(ctx, args):
            task, context = await load(ctx)
            state, track = context["state"], context["track"]
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
                        "status": (
                            "judged"
                            if pages_.complete(page, context["cues"], state["judgements"])
                            else "glossed"
                            if pages_.glossed(page, context["utterances"], state["glosses"])
                            else "to do"
                        ),
                        **({"written": len(state["written"][key])} if key in state.get("written", {}) else {}),
                    }
                )
            following = pages_.next_page(state, state["pages"], context["cues"])
            return {
                "track_in_use": (
                    {
                        "source": track["data"]["source_id"],
                        "title": track["data"].get("title", ""),
                        "captions": len(context["cues"]),
                        "changes_so_far": len(track["data"].get("edits") or []),
                    }
                    if track
                    else "none — no usable subtitles were found; write them with write_page and use_written"
                ),
                "wanted": {"language": task["data"]["language"], "style": task["data"]["kind"]},
                "spoken_languages": context["evidence"]["coverage"].get("languages", {}),
                "captions_revealed_without_gloss": context["same_language"],
                "timing": pages_.offsets_summary(context["measured"]),
                "sources": {"total": len(task["data"].get("candidates", [])), "list_with": "sources"},
                "verdicts_needed": {
                    str(p["number"]): pages_.unjudged(p, context["cues"], state["judgements"])
                    for p in state["pages"]
                    if pages_.unjudged(p, context["cues"], state["judgements"])
                },
                "pages": listing,
                "progress": pages_.summary(state, state["pages"]),
                "next": (
                    f"Call page with page={following['number']}."
                    if following
                    else "Every page is judged; fix anything outstanding, then call verdict."
                ),
            }

        async def page(ctx, args):
            task, context = await load(ctx)
            found = find_page(context, args.get("page"))
            shown = view(ctx, context, found)
            self.review_state(task, context["state"])
            return shown

        async def gloss(ctx, args):
            task, context = await load(ctx)
            found = find_page(context, args.get("page"))
            state = context["state"]
            try:
                state["glosses"].update(
                    pages_.check_gloss(found, context["utterances"], args.get("lines", []))
                )
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            missing = [
                u["id"]
                for u in pages_.page_utterances(found, context["utterances"])
                if u["id"] not in state["glosses"]
            ]
            if missing:
                self.review_state(task, state)
                raise ToolError("Gloss every line on this page first; missing: " + ", ".join(missing[:12]))
            shown = view(ctx, context, found)
            self.review_state(task, state)
            return shown

        async def judge(ctx, args):
            task, context = await load(ctx)
            found = find_page(context, args.get("page"))
            state = context["state"]
            key = str(found["number"])
            if state.get("contract") and found["number"] not in (state.get("audit") or {}).get("pages", []):
                raise ToolError("Judge only audit pages; settle other captions with resolve.")
            if not context["same_language"] and not pages_.glossed(found, context["utterances"], state["glosses"]):
                raise ToolError("Gloss this page's speech before judging its captions.")
            seen = state.get("shown", {}).get(key)
            if not seen or seen[0] != ctx.session.id or seen[1] >= len(ctx.session.messages):
                raise ToolError(
                    "Read this page's captions before judging them: call page for it, then judge in a later step."
                )
            already = state["judgements"].get(key, {})
            try:
                judged, gaps = pages_.check_judgement(
                    found,
                    context["utterances"],
                    context["cues"],
                    state["listens"],
                    args.get("captions", []),
                    args.get("missing"),
                    already,
                )
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            for entry in judged.values():
                entry["by"] = "manager"
            state["judgements"][key] = {**already, **judged}
            if gaps is not None or key not in state["missing"]:
                state["missing"][key] = gaps or []
            audit = state.get("audit") or {}
            if found["number"] in audit.get("pages", []) and found["number"] not in audit.get("done", []):
                misses = pages_.audit_outcome(
                    (state.get("contract") or {}).get("snapshot", {}).get(key, {}), judged, gaps or []
                )
                audit.setdefault("done", []).append(found["number"])
                if misses:
                    # The checkers missed something here: audit more pages.
                    audit.setdefault("misses", []).extend(f"page {key}: {m}" for m in misses)
                    extra = pages_.choose_audits(
                        state["pages"], context["cues"], context["utterances"],
                        task["id"] + str(len(audit["pages"])), AUDIT_PAGES, exclude=audit["pages"],
                    )
                    audit["pages"] = sorted(audit["pages"] + extra)
            following = pages_.next_page(state, state["pages"], context["cues"])
            progress = pages_.summary(state, state["pages"])
            if not following:
                self.review_state(task, state)
                return f"Recorded page {key}. Every page is judged: {json.dumps(progress)}. Fix anything outstanding, then call verdict."
            shown = view(ctx, context, following)
            self.review_state(
                task,
                state,
                f"Checking the subtitles against the dialogue ({len(state['judgements'])} of {len(state['pages'])} pages).",
            )
            return f"Recorded page {key}. Progress: {json.dumps(progress)}.\n\n" + shown

        async def listen(ctx, args):
            task, context = await load(ctx)
            state = context["state"]
            start, end = float(args.get("start", -1)), float(args.get("end", -1))
            try:
                pages_.listen_allowance(state, start, end)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            asset = context["asset"]
            key = context["info"]["path"].split("/")[1]
            try:
                heard = await self.nodes.execute(
                    context["node"],
                    "subtitle_listen",
                    {
                        "root_id": asset["root_id"],
                        "path": asset["path"],
                        "evidence": key,
                        "start": start,
                        "end": end,
                        "language": args.get("language") or None,
                        "model": args.get("model", "default"),
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

        async def sources(ctx, args):
            task, context = await load(ctx)
            return {"sources": sources_list(task, context)}

        async def search_online(ctx, args):
            task, context = await load(ctx)
            provider = SubtitleProvider(self.provider())
            if not provider.settings.get("api_key"):
                raise ToolError(
                    "No online subtitle provider is configured. Use the local sources, or write the subtitles."
                )
            user, asset = self.authority(task)
            item = self.catalogue.item(user, asset["item_id"])
            if not item:
                raise ToolError("This title's details are unavailable for an online search.")
            kind = args.get("style") or task["data"]["kind"]
            try:
                found = await provider.search(
                    asset, item, task["data"]["language"], kind, task["data"].get("movie_hash")
                )
            except (ToolError, httpx.HTTPError) as exc:
                raise ToolError(f"The online search failed: {exc}") from exc
            known = {c["id"] for c in task["data"].get("candidates", [])}
            added = [c for c in found if c["id"] not in known]
            if added:
                self.update(
                    task,
                    task["state"],
                    task["data"].get("message", ""),
                    candidates=task["data"].get("candidates", []) + added,
                )
            return {"added": len(added), "sources": sources_list(self.task(task["id"]), context)}

        async def use_source(ctx, args):
            task, context = await load(ctx)
            identity = str(args.get("source", ""))
            candidate = next((c for c in task["data"].get("candidates", []) if c["id"] == identity), None)
            if not candidate:
                raise ToolError("Choose a source ID from sources.")
            asset = context["asset"]
            try:
                text, format_name = await self.fetch_text(task, asset, candidate)
                new_id = await self.prepare(
                    task, asset, candidate, text, format_name, task["data"]["audio_index"]
                )
            except (NodeError, ToolError, httpx.HTTPError, ValueError) as exc:
                attempts = task["data"].get("attempts", []) + [
                    {"source_id": identity, "outcome": "failed", "reason": str(exc)[:500]}
                ]
                self.update(task, "reviewing", task["data"].get("message", ""), attempts=attempts)
                raise ToolError(f"That source could not be prepared: {exc}") from exc
            previous = context["track"]
            attempts = [
                {**a, "outcome": "set_aside", "reason": "Replaced by the subtitle agent."}
                if previous and a.get("source_id") == previous["data"].get("source_id")
                else a
                for a in task["data"].get("attempts", [])
            ] + [{"source_id": identity, "track_id": new_id, "outcome": "prepared"}]
            self.update(task, "reviewing", task["data"].get("message", ""), attempts=attempts)
            if previous:
                self.save_track(previous["id"], state="rejected")
            task = self.task(task["id"])
            context = await self.review_context(task, asset)
            new = self.track(new_id)
            cues, measured = await self.measured(context["node"], new, context["evidence"], context["info"])
            fix = timing_correction(measured)
            if fix:
                context["track"], context["cues"] = new, cues
                new_id = await self.derive(context, pages_.retime(cues, measured, "drift" if fix["kind"] == "drift" else "shift")[0], {"kind": fix["kind"], "source": "measured"})
            measured, pages, _ = await self.activate(context, new_id, f"use_source {identity}", rebuild=True)
            if contractor_model():
                context["state"]["audit"] = None
                context["track"] = self.track(new_id)
                await self.contract(context)
                task = self.task(task["id"])
                context = await self.review_context(task, context["asset"])
                return {
                    "now_using": identity,
                    "timing": pages_.offsets_summary(context["measured"]),
                    "measured_correction_applied": bool(fix),
                    "next": "The checkers re-read this track; call report.",
                }
            return {
                "now_using": identity,
                "timing": pages_.offsets_summary(measured),
                "measured_correction_applied": bool(fix),
                "pages": len(pages),
                "next": "Pages were rebuilt for this track; your readings are kept. Call page with page=1.",
            }

        async def retime_tool(ctx, args):
            task, context = await load(ctx)
            if not context["track"]:
                raise ToolError("No track is in use; choose a source or write the subtitles.")
            seconds = args.get("seconds")
            try:
                cues, detail = pages_.retime(
                    context["cues"],
                    context["measured"],
                    args.get("mode", "shift"),
                    None if seconds is None else float(seconds),
                )
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            before = pages_.offsets_summary(context["measured"])
            new_id = await self.derive(context, cues, {"kind": "retime", "detail": detail})
            measured, pages, reopen = await self.activate(context, new_id, "retime " + detail, rebuild=False)
            return {
                "applied": detail,
                "before": before,
                "after": pages_.offsets_summary(measured),
                "pages_needing_verdicts": reopen,
            }

        async def edit_captions(ctx, args):
            task, context = await load(ctx)
            if not context["track"]:
                raise ToolError("No track is in use; choose a source or write the subtitles.")
            try:
                cues, touched, renamed = pages_.edit(
                    context["cues"],
                    context["utterances"],
                    context["state"]["listens"],
                    args.get("changes", []),
                    args.get("add", []),
                    args.get("replace", []),
                )
            except (ValueError, TypeError) as exc:
                raise ToolError(str(exc)) from exc
            detail = {
                "kind": "edit",
                "changed": len(args.get("changes", [])),
                "added": len(args.get("add", [])),
                "replaced": len(renamed),
            }
            new_id = await self.derive(context, cues, detail)
            measured, pages, reopen = await self.activate(
                context, new_id, "edit captions", rebuild=False, renamed=renamed
            )
            state = context["state"]
            if len(cues) == len(context["cues"]):
                covered = set()
                for index, cue in enumerate(cues):
                    verdict = cue.get("manager_verdict")
                    if verdict:
                        page_ = pages_.page_of(pages, context["cues"][index]["start"])
                        state["judgements"].setdefault(str(page_["number"]), {})[pages_.caption_id(index)] = verdict
                        covered.update(verdict["speech"])
                for key, items in state.get("missing", {}).items():
                    state["missing"][key] = [g for g in items if not set(g["speech"]) <= covered]
                reopen = [
                    p["number"] for p in pages if not pages_.complete(p, context["cues"], state["judgements"])
                ]
            views = []
            for number in reopen[:2]:
                # Show the captions that need verdicts, so they can be judged next.
                views.append(view(ctx, context, pages[number - 1]))
            self.review_state(task, state)
            return (
                json.dumps(
                    {
                        "captions": len(cues),
                        "replaced_in": len(renamed),
                        "timing": pages_.offsets_summary(measured)["overall_seconds_vs_voice"]
                        if measured.get("measurable")
                        else "not measurable",
                        "verdicts_needed": {
                            str(n): pages_.unjudged(pages[n - 1], context["cues"], state["judgements"])
                            for n in reopen
                        },
                    }
                )
                + ("\n\n" + "\n\n".join(views) if views else "\nNo verdicts needed; call verdict when ready.")
            )

        async def write_page(ctx, args):
            task, context = await load(ctx)
            found = find_page(context, args.get("page"))
            state = context["state"]
            try:
                state["written"][str(found["number"])] = pages_.check_written(
                    found, context["utterances"], state["listens"], args.get("captions", [])
                )
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            spoken = [p for p in state["pages"] if pages_.page_utterances(p, context["utterances"])]
            remaining = [p for p in spoken if str(p["number"]) not in state["written"]]
            state["actions"] = (state.get("actions") or [])[-40:] + [{"action": f"write_page {found['number']}", "at": time.time()}]
            self.review_state(task, state, "Writing subtitles from the dialogue.")
            if not remaining:
                return "Every page with speech is written. Call use_written to build the track."
            return (
                f"Recorded page {found['number']}; {len(remaining)} pages with speech remain.\n\n"
                + pages_.speech_view(remaining[0], len(state["pages"]), context["utterances"], state["listens"])
            )

        async def use_written(ctx, args):
            task, context = await load(ctx)
            state = context["state"]
            spoken = [p for p in state["pages"] if pages_.page_utterances(p, context["utterances"])]
            remaining = [p["number"] for p in spoken if str(p["number"]) not in state["written"]]
            if remaining:
                raise ToolError("Write every page with speech first; remaining: " + ", ".join(map(str, remaining[:12])))
            cues = pages_.compose(state["pages"], state["written"], context["utterances"], state["listens"])
            if not cues:
                raise ToolError("No captions were written.")
            from .subtitle_worker import render

            candidate = {"id": "written", "source": "written", "title": "Written by Sparrow from the dialogue"}
            new_id = await self.prepare(
                task,
                context["asset"],
                candidate,
                render(cues, vtt=True),
                "vtt",
                task["data"]["audio_index"],
                suffix=":" + hashlib.sha256(json.dumps(state["written"], sort_keys=True).encode()).hexdigest()[:16],
            )
            if context["track"]:
                self.save_track(context["track"]["id"], state="rejected")
            measured, pages, _ = await self.activate(context, new_id, "use_written", rebuild=True)
            fresh = self.task(task["id"])
            state = fresh["data"]["review"]
            track = self.track(new_id)
            new_cues = await self.cues(context["node"], track["data"]["path"], track["data"]["version"])
            if len(new_cues) != len(cues):
                raise ToolError("The written track did not prepare as composed; write the pages again.")
            # The agent wrote each caption for the speech it cites; record that
            # correspondence. It can still view pages and edit before verdict.
            for page in pages:
                state["judgements"][str(page["number"])] = {
                    pages_.caption_id(i): {"verdict": "ok", "speech": cues[i]["speech"], "note": "written from the dialogue"}
                    for i, _ in pages_.page_captions(page, new_cues)
                }
                state["missing"][str(page["number"])] = []
            self.review_state(fresh, state, "Checking the written subtitles.")
            return {
                "captions": len(new_cues),
                "timing": pages_.offsets_summary(measured),
                "next": "Review any page with page, fix with edit_captions if needed, then call verdict.",
            }

        async def report_tool(ctx, args):
            task, context = await load(ctx)
            state = context["state"]
            data = pages_.report(state, state["pages"], context["cues"], context["utterances"], context["measured"])
            mark_seen(ctx, state, [row["caption"] for row in data["to_settle"]])
            self.review_state(task, state)
            return data

        async def resolve(ctx, args):
            task, context = await load(ctx)
            state = context["state"]
            speech = pages_.known_speech(context["utterances"], state["listens"])
            ids = {pages_.caption_id(i) for i in range(len(context["cues"]))}
            settled = 0
            for item in args.get("settle", []):
                caption = str(item.get("caption", ""))
                if caption not in ids:
                    raise ToolError(f"{caption or 'A caption'} does not exist.")
                seen = state.get("seen", {}).get(caption)
                if not seen or seen[0] != ctx.session.id or seen[1] >= len(ctx.session.messages):
                    raise ToolError(f"Look at {caption} (report or page) before settling it.")
                verdict_ = item.get("verdict")
                cited = [str(x) for x in item.get("speech", [])]
                if verdict_ not in pages_.VERDICTS or any(x not in speech for x in cited):
                    raise ToolError(f"{caption}: use a known verdict and recognised speech IDs.")
                if verdict_ in ("ok", "loose") and not cited:
                    raise ToolError(f"{caption}: cite the speech it corresponds to.")
                index = pages_.caption_index(caption)
                page_ = pages_.page_of(state["pages"], context["cues"][index]["start"])
                state["judgements"].setdefault(str(page_["number"]), {})[caption] = {
                    "verdict": verdict_, "speech": cited, "note": str(item.get("note", ""))[:300], "by": "manager",
                }
                settled += 1
            dismissed = 0
            for item in args.get("dismiss_missing", []):
                key, wanted = str(item.get("page", "")), set(map(str, item.get("speech", [])))
                before = state.get("missing", {}).get(key, [])
                state.setdefault("missing", {})[key] = [g for g in before if set(g["speech"]) != wanted]
                dismissed += len(before) - len(state["missing"][key])
            self.review_state(task, state)
            remaining = pages_.flagged(state, state["pages"], context["cues"])
            return {
                "settled": settled,
                "dismissed": dismissed,
                "still_to_settle": len(remaining),
                "missing_dialogue": sum(len(v) for v in state.get("missing", {}).values()),
            }

        async def verdict(ctx, args):
            task, context = await load(ctx)
            approved = args.get("approved") is True
            reason = str(args.get("reason", "")).strip()
            if not 1 <= len(reason) <= 1000:
                raise ToolError("Give a short plain reason for the verdict.")
            state = context["state"]
            if approved:
                if not context["track"]:
                    raise ToolError("Approval refused: no track is in use.")
                check = pages_.manager_gate if state.get("contract") else pages_.gate
                reasons = check(
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
                "track": context["track"] and context["track"]["id"],
                "approved": approved,
                "reason": reason,
                "summary": pages_.summary(state, state["pages"]),
                "session_id": ctx.session.id,
                "model": ctx.session.model,
                "reviewed_at": time.time(),
            }
            if context["track"]:
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
        ids = {"type": "array", "items": {"type": "string"}}
        text = {"type": "string"}
        tools = [
            ToolDef("overview", "Track in use, measured timing overall and per section, pages and progress. Call first.", obj({}, []), overview),
            ToolDef("sources", "Every subtitle source for this title: embedded, beside the file, online or written, with status.", obj({}, []), sources),
            ToolDef(
                "page",
                "Show one page: recognised speech only until you gloss it, then speech with your readings and the captions with measured timing.",
                obj({"page": {"type": "integer", "minimum": 1}}, ["page"]),
                page,
            ),
            ToolDef(
                "gloss",
                "Record a brief English reading of every recognised speech line on a page; returns the page with its captions revealed.",
                obj(
                    {
                        "page": {"type": "integer", "minimum": 1},
                        "lines": {"type": "array", "items": obj({"speech": text, "english": text}, ["speech", "english"])},
                    },
                    ["page", "lines"],
                ),
                gloss,
            ),
            ToolDef(
                "judge",
                "Record verdicts for a page you have read: every caption not yet judged (others may be re-judged), and uncaptioned dialogue (omit missing to keep the earlier list). Returns the next page needing verdicts.",
                obj(
                    {
                        "page": {"type": "integer", "minimum": 1},
                        "captions": {
                            "type": "array",
                            "items": obj(
                                {"caption": text, "verdict": {"type": "string", "enum": list(pages_.VERDICTS)}, "speech": ids, "note": text},
                                ["caption", "verdict", "speech"],
                            ),
                        },
                        "missing": {"type": "array", "items": obj({"speech": ids, "note": text}, ["speech"])},
                    },
                    ["page", "captions"],
                ),
                judge,
            ),
            ToolDef(
                "retime",
                "Move the track to follow the voice: shift (measured, or by seconds), drift (measured), or sections (each section by its measured offset). Re-measures and keeps page judgements.",
                obj({"mode": {"type": "string", "enum": ["shift", "drift", "sections"]}, "seconds": {"type": "number"}}, ["mode"]),
                retime_tool,
            ),
            ToolDef(
                "edit_captions",
                "Change captions: new text, align_to speech line IDs, nudge by seconds, or remove; add captions for uncaptioned speech; replace a word or phrase across the whole track (whole words, case-sensitive). Batch fixes in one call. Re-measures and shows captions needing new verdicts.",
                obj(
                    {
                        "changes": {
                            "type": "array",
                            "items": obj(
                                {
                                    "caption": text,
                                    "text": text,
                                    "align_to": ids,
                                    "nudge": {"type": "number"},
                                    "remove": {"type": "boolean"},
                                    "verdict": {"type": "string", "enum": list(pages_.VERDICTS)},
                                    "speech": ids,
                                },
                                ["caption"],
                            ),
                        },
                        "add": {"type": "array", "items": obj({"speech": ids, "text": text}, ["speech", "text"])},
                        "replace": {"type": "array", "items": obj({"find": text, "with": text}, ["find", "with"])},
                    },
                    [],
                ),
                edit_captions,
            ),
            ToolDef("use_source", "Switch to another subtitle source by ID; it is prepared, measured and timing-corrected.", obj({"source": text}, ["source"]), use_source),
            ToolDef("search_online", "Search the configured online provider for more sources.", obj({"style": {"type": "string", "enum": ["full", "sdh", "forced"]}}, []), search_online),
            ToolDef(
                "write_page",
                "Write your own English captions for one page, each citing the speech lines it translates. Timing comes from that speech.",
                obj(
                    {
                        "page": {"type": "integer", "minimum": 1},
                        "captions": {"type": "array", "items": obj({"speech": ids, "text": text}, ["speech", "text"])},
                    },
                    ["page", "captions"],
                ),
                write_page,
            ),
            ToolDef("use_written", "Build a track from your written pages and put it in use.", obj({}, []), use_written),
            ToolDef(
                "listen",
                "Re-recognise up to 60 seconds of the soundtrack with a language hint or another model (alternative or large). Adds evidence; nothing is replaced.",
                obj(
                    {
                        "start": {"type": "number", "minimum": 0},
                        "end": {"type": "number", "minimum": 0},
                        "language": text,
                        "model": {"type": "string", "enum": ["default", "alternative", "large"]},
                    },
                    ["start", "end"],
                ),
                listen,
            ),
            ToolDef(
                "verdict",
                "Approve when the evidence shows the subtitles right, or reject if they truly cannot be made right. Approval is refused until they are.",
                obj({"approved": {"type": "boolean"}, "reason": text}, ["approved", "reason"]),
                verdict,
            ),
        ]
        manager_tools = [t for t in tools if t.name not in ("overview", "sources")] + [
            ToolDef(
                "report",
                "Timing, suggested fixes, every flagged caption with its speech and the checker's translation, missing dialogue and audit pages. Call first.",
                obj({}, []),
                report_tool,
            ),
            ToolDef("sources", "Every subtitle source for this title, with status.", obj({}, []), sources),
            ToolDef(
                "resolve",
                "Settle captions you have looked at with your own verdict (citing speech), and dismiss missing-dialogue items that are not substantive dialogue.",
                obj(
                    {
                        "settle": {
                            "type": "array",
                            "items": obj(
                                {"caption": text, "verdict": {"type": "string", "enum": list(pages_.VERDICTS)}, "speech": ids, "note": text},
                                ["caption", "verdict", "speech"],
                            ),
                        },
                        "dismiss_missing": {
                            "type": "array",
                            "items": obj({"page": {"type": "integer"}, "speech": ids, "reason": text}, ["page", "speech", "reason"]),
                        },
                    },
                    [],
                ),
                resolve,
            ),
        ]

        def with_ids(handler):
            # Models sometimes quote a line's or caption's text where its ID
            # belongs; the tool resolves unambiguous quotes instead of failing.
            async def run(ctx, args):
                _, context = await load(ctx)
                listens = context["state"].get("listens", [])
                return await handler(ctx, pages_.normalise_ids(args, context["utterances"], context["cues"], listens))

            return run

        for tool in {id(t): t for t in tools + manager_tools}.values():
            if tool.name in ("gloss", "judge", "edit_captions", "write_page", "resolve"):
                tool.handler = with_ids(tool.handler)

        def toolset(session):
            return manager_tools if managing(session) else tools

        service.runtime.register(
            AgentSpec(
                AgentKind.SUBTITLE.value,
                review_model,
                system,
                toolset,
                max_steps=40,
                max_tokens=16000,
                cache=True,
                effort=os.getenv("SPARROW_SUBTITLE_EFFORT") or "medium",
                max_dollars=float(os.getenv("SPARROW_SUBTITLE_BUDGET") or 0),  # 0: the household policy.
            )
        )

    # ─── The task ─────────────────────────────────────────────────────────

    def set_aside(self, task, track, reason, *, fallback=False):
        attempts = [
            {**a, "outcome": "set_aside", "reason": reason}
            if a.get("source_id") == track["data"].get("source_id")
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
        self.save_track(track_id, state="ready")
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
        if checked and data.get("source") == "written":
            message = "Subtitles written from the dialogue and checked."
        elif checked:
            message = "Subtitles checked against the dialogue."
            if data.get("edits"):
                message += " Corrections were made to match it."
            elif data.get("correction"):
                message += " Their timing was adjusted to follow the voice."
        elif timing.get("within_tolerance") and data.get("correction"):
            message = "Subtitles ready. Their timing was adjusted to follow the voice."
        elif timing.get("within_tolerance"):
            message = "Subtitles ready and in time with the voice."
        else:
            message = "Subtitles ready."
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
            for _ in range(3 * MAX_CANDIDATES + 6):
                task = self.task(identity)
                user, asset = self.authority(task)
                data = task["data"]
                track = self.track(data.get("track_id"))
                usable = bool(track and track["state"] == "ready")
                if not usable and not data.get("writing"):
                    if await self.find(task, user, asset):
                        continue
                    task = self.task(identity)
                    data = task["data"]
                    if data.get("upload_failed"):
                        self.update(task, "needs_attention", data["upload_failed"])
                        return
                    if data.get("verify") and data.get("audio_index") is not None:
                        # No usable source: the subtitle agent writes them from the dialogue.
                        self.update(task, "measuring", "No subtitles found. Listening to the dialogue to write them.", writing=True)
                        continue
                    fallback = self.track(data.get("fallback"))
                    if fallback and fallback["state"] == "ready":
                        self.finish(task, fallback["id"], note="Their timing could not be matched to the dialogue.")
                        await self.notify_ready(self.task(identity))
                        return
                    reason = next(
                        (a["reason"] for a in reversed(data.get("attempts", [])) if a.get("reason")),
                        "No matching subtitle was found. Add a subtitle file or configure a provider.",
                    )
                    self.update(task, "needs_attention", reason)
                    return
                if usable and self.wants_analysis(task, asset) and data.get("measured") != track["id"]:
                    outcome = await self.measure(task, asset, track)
                    if outcome == "inconsistent" and not data.get("verify"):
                        self.set_aside(task, track, "Its timing does not follow this title's dialogue.", fallback=True)
                    elif outcome == "inconsistent":
                        # The subtitle agent decides what to do with it.
                        self.update(self.task(identity), "measuring", "Its timing does not follow the dialogue.", measured=track["id"])
                    continue
                if not data.get("verify"):
                    self.finish(task, track["id"])
                    await self.notify_ready(self.task(identity))
                    return
                if not data.get("speech"):
                    info = await self.evidence(task, asset)
                    if not info or info.get("error"):
                        if usable:
                            self.finish(task, track["id"], note="They could not be checked: dialogue analysis is unavailable.")
                            await self.notify_ready(self.task(identity))
                        else:
                            self.update(task, "needs_attention", "No subtitles were found, and the dialogue could not be analysed to write them.")
                        return
                    self.update(
                        task,
                        "measuring",
                        "Listening complete.",
                        speech={k: info[k] for k in ("path", "version", "model", "coverage")},
                    )
                    continue
                outcome = data.get("review_outcome")
                if not outcome:
                    outcome = await self.review(task)
                if outcome is None:
                    return
                task = self.task(identity)
                if outcome["approved"] and outcome.get("track"):
                    self.finish(task, outcome["track"], checked=True)
                    await self.notify_ready(self.task(identity))
                    return
                rejected = self.track(task["data"].get("track_id") or outcome.get("track"))
                if rejected and rejected["data"].get("source_id") != "written":
                    # Try the next source; with none left the dialogue is written.
                    self.set_aside(task, rejected, outcome["reason"])
                    continue
                if rejected:
                    self.save_track(rejected["id"], state="rejected")
                self.update(task, "needs_attention", outcome["reason"])
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
