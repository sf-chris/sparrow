"""
The tool belt. Code lives here; intelligence lives in the loop.

Every tool is honest plumbing: it reports what actually happened and never
decides anything. Guardrails (filesystem jail, indexer rate limits, the
upgrade-swap-only deletion rule) are enforced here at the tool layer —
an agent's good judgment is not a substitute for a seatbelt on rm.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Callable, Optional

import httpx

from ..models import Download, DownloadStatus, MediaType, LibraryItem, quality_rank
from ..storage import Storage
from ..services.torrent_client import TorrentManager, build_magnet, match_files, select_files, start_configured_client
from ..services.search_engine import _query as apibay_query
from ..services.release_parser import parse_release_name
from .models import (AgentKind, AgentSession, Event, JournalEntry, JobStatus,
                     MonitoringMode)
from .runtime import ToolCtx, ToolDef, ToolError
from .store import AgentStore
from .accounts import Accounts

logger = logging.getLogger("sparrow.agents")
from .media_state import media_state, file_version, audio_satisfies

TMDB = "https://api.themoviedb.org/3"

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov", ".webm"}


def _apibay_indexed_value(value, default=None):
    """Read APIBay file fields across its observed dict/list response shapes."""
    if isinstance(value, dict):
        return value.get("0", value.get(0, default))
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


async def _probe_media_facts(path: Path) -> dict:
    """Read duration and raster quality from ffprobe as ground truth."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=index,codec_name,codec_type,width,height:stream_tags=language,title", "-of", "json", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except FileNotFoundError as exc:
        raise ToolError("ffprobe is required before Sparrow can replace library media.") from exc
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise ToolError(f"ffprobe timed out while verifying {path.name}.") from exc
    if proc.returncode != 0:
        raise ToolError(f"Cannot replace library media: {path.name} is not readable media.")
    try:
        payload = json.loads(stdout.decode())
        duration = float((payload.get("format") or {}).get("duration"))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ToolError(f"Cannot replace library media: {path.name} has no usable duration.") from exc
    if duration <= 0:
        raise ToolError(f"Cannot replace library media: {path.name} has no usable duration.")
    video = next(
        (stream for stream in payload.get("streams", [])
         if stream.get("codec_type") == "video"),
        {},
    )
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    short_side = min(width, height) if width and height else 0
    if short_side >= 1600:
        quality = "2160p"
    elif short_side >= 800:
        quality = "1080p"
    elif short_side >= 570:
        quality = "720p"
    elif short_side:
        quality = "480p"
    else:
        quality = "unknown"
    return {"duration_seconds": duration, "width": width, "height": height,
            "quality": quality,
            "audio_languages": [(s.get("tags") or {}).get("language", "und")
                                for s in payload.get("streams", []) if s.get("codec_type") == "audio"],
            "audio_tracks": [{"index": s.get("index"), "codec": s.get("codec_name"),
                              "language": (s.get("tags") or {}).get("language", "und"),
                              "title": (s.get("tags") or {}).get("title", "")}
                             for s in payload.get("streams", []) if s.get("codec_type") == "audio"]}


async def _probe_duration_seconds(path: Path) -> float:
    """Read a media duration for a destructive guardrail, not agent judgment."""
    return float((await _probe_media_facts(path))["duration_seconds"])


def remove_download_staging(staging_dir: str, download) -> None:
    """Delete a download's isolated staging folder, and only that folder.

    Refuses anything that isn't a strict subdirectory of the configured
    staging root, so legacy downloads sharing the root are never touched.
    """
    if not staging_dir or not download.staging_path:
        return
    base = Path(staging_dir).resolve()
    own = Path(download.staging_path).resolve()
    if own != base and base in own.parents and own.exists():
        shutil.rmtree(own, ignore_errors=True)


class Toolbox:
    """Shared dependencies for all tool handlers, wired once at startup."""

    def __init__(self, storage: Storage, store: AgentStore,
                 emit: Callable[[Event], "asyncio.Future | None"],
                 broadcast: Callable[[dict], "asyncio.Future | None"]):
        self.storage = storage
        self.accounts = Accounts(storage.data_dir)
        self.store = store
        self.emit = emit              # async callable: push an Event onto the bus
        self.broadcast = broadcast    # async callable: websocket fanout to the UI
        self.transfer_lock = asyncio.Lock()
        self._search_times: list[float] = []   # indexer rate limiting (global)

    # ─── shared helpers ──────────────────────────────────────────────────

    def require_authority(self, ctx: ToolCtx):
        job = self.job_for(ctx.session)
        if not job or job.status != JobStatus.ACTIVE or job.revision != ctx.job_revision:
            raise ToolError("This request is paused, cancelled or changed. Its previous authority has expired.")
        if job.user_id:
            user = self.accounts.user(job.user_id)
            if not user or user['role'] not in ('admin', 'requester') or not self.accounts.can_access(user, job.library_id):
                raise ToolError("The requesting account no longer has permission to manage this library.")
            policy=self.accounts.server_settings()['policy']
            snapshot=job.preferences.get('policy',{})
            if policy.get('max_quality')!=snapshot.get('max_quality') or policy.get('max_file_size_gb')!=snapshot.get('max_file_size_gb'):
                raise ToolError('Server media limits changed. Update this request to apply the current policy before continuing acquisition or publication.')
        current = self.store.get_session(ctx.session.id)
        if current and current.status.value == "closed":
            raise ToolError("This agent session is closed.")
        return job

    def cfg(self):
        return self.storage.get_config()

    def tmdb_key(self) -> str:
        return self.cfg().tmdb_api_key or os.getenv("TMDB_API_KEY", "")

    def torrents(self) -> TorrentManager:
        return TorrentManager(self.cfg().torrent_client)

    def smart_model(self) -> str:
        return (os.getenv("SPARROW_SMART_MODEL") or self.cfg().smart_model
                or "claude-sonnet-5")

    async def apply_file_selection(self, download) -> Optional[dict]:
        """Download only the files the agent chose from a pack.

        Runs on the storage node that holds the transfer once the torrent's
        file list is known (magnets fetch it first); returns the recorded
        selection, or None while still waiting. When none of the chosen files
        exist the transfer is stopped and the request woken.
        """
        wanted = download.metadata.get("wanted_files") or []
        if not wanted or download.metadata.get("selection"):
            return download.metadata.get("selection")
        node_id = download.metadata.get("node_id")
        if node_id:
            from .node_tools import components

            nodes, _ = components(self)
            job = self.store.get_job(download.metadata.get("job_id", ""))
            result = await nodes.execute(node_id, "download_select",
                                         {"hash": download.torrent_hash, "files": wanted}, job=job, timeout=20)
        else:
            result = await select_files(self.torrents(), download.torrent_hash, wanted)
        if not result or result.get("pending"):
            return None
        values = {}
        if "error" in result:
            values = {"status": DownloadStatus.ERROR,
                      "error_message": "None of the chosen files are in this torrent; nothing was downloaded."}
        current = self.storage.get_download(download.id) or download
        await self.storage.update_download(download.id, metadata={**current.metadata, "selection": result}, **values)
        if "error" in result:
            await self.emit(Event(
                kind="download_stalled", job_id=download.metadata.get("job_id", ""), download_id=download.id,
                payload={"description": f'None of the files you chose are in "{download.name}", so it was stopped. '
                                        f'Its files include: {", ".join(result.get("available", [])[:12])}. '
                                        "Remove it and choose again.", "download_id": download.id}))
        return result

    # ─── Waiting for a transfer slot ────────────────────────────────────

    SLOT_PROMISE = 180.0  # seconds a woken request has to take its slot

    def _waiters(self, db):
        db.execute("CREATE TABLE IF NOT EXISTS transfer_waiters("
                   "job_id TEXT PRIMARY KEY, since REAL NOT NULL, woken REAL NOT NULL DEFAULT 0)")

    def wait_for_slot(self, job_id: str) -> None:
        """Queue a request that hit the transfer limit; it keeps its place."""
        with self.store._connect() as db:
            self._waiters(db)
            db.execute("INSERT INTO transfer_waiters VALUES (?, ?, 0) "
                       "ON CONFLICT(job_id) DO UPDATE SET woken=0", (job_id, time.time()))

    def slot_taken(self, job_id: str) -> None:
        with self.store._connect() as db:
            self._waiters(db)
            db.execute("DELETE FROM transfer_waiters WHERE job_id=?", (job_id,))

    def slots_to_offer(self) -> list[str]:
        """The longest-waiting requests to wake for free transfer slots.

        A woken request holds its promise briefly so one slot wakes one
        request; a request that lets the promise lapse leaves the queue.
        """
        limit = max(1, int(self.cfg().max_active_transfers or 1))
        active = sum(1 for d in self.storage.get_all_downloads()
                     if d.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED))
        now = time.time()
        with self.store._connect() as db:
            self._waiters(db)
            db.execute("DELETE FROM transfer_waiters WHERE woken>0 AND woken<?", (now - self.SLOT_PROMISE,))
            promised = db.execute("SELECT COUNT(*) FROM transfer_waiters WHERE woken>0").fetchone()[0]
            free = limit - active - promised
            if free <= 0:
                return []
            rows = db.execute("SELECT job_id FROM transfer_waiters WHERE woken=0 ORDER BY since LIMIT ?", (free,)).fetchall()
            chosen = [r["job_id"] for r in rows]
            db.executemany("UPDATE transfer_waiters SET woken=? WHERE job_id=?", [(now, j) for j in chosen])
        return chosen

    async def select_soon(self, download_id: str, seconds: float = 120.0) -> None:
        """Apply a pack selection as soon as the file list arrives; the poller
        finishes the job if it takes longer or the server restarts."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            download = self.storage.get_download(download_id)
            if not download or download.metadata.get("selection"):
                return
            try:
                if await self.apply_file_selection(download):
                    return
            except Exception:
                logger.debug("pack selection not applied yet", exc_info=True)
            await asyncio.sleep(2)

    async def connect_torrents(self) -> tuple[TorrentManager, bool, str]:
        """Connect, starting an installed local client when it is merely inactive."""
        config = self.cfg().torrent_client
        manager = TorrentManager(config)
        if await manager.connect():
            return manager, True, "Download app is reachable."
        started, message = await start_configured_client(config)
        if started:
            for _ in range(8):
                await asyncio.sleep(1)
                if await manager.connect():
                    return manager, True, message
            message = f"{message} It opened, but its remote access is not responding."
        return manager, False, message

    async def tmdb_get(self, path: str, **params) -> dict:
        key = self.tmdb_key()
        if not key:
            raise ToolError("TMDB API key is not configured (Settings → TMDB).")
        params["api_key"] = key
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.get(f"{TMDB}{path}", params=params)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as e:
            raise ToolError(f"TMDB returned {e.response.status_code} for {path}")
        except httpx.HTTPError as e:
            raise ToolError(f"TMDB unreachable: {e}")

    async def rate_limit_search(self) -> None:
        """Never hammer the indexer into rate-limiting the user's IP."""
        now = time.time()
        self._search_times = [t for t in self._search_times if now - t < 600]
        if len(self._search_times) >= 30:
            raise ToolError(
                "Indexer rate limit: 30 searches per 10 minutes. You have been "
                "searching heavily — step back, think about what you've learned, "
                "write it to memory, and hibernate with a wake timer.")
        if self._search_times and now - self._search_times[-1] < 1.5:
            await asyncio.sleep(1.5 - (now - self._search_times[-1]))
        self._search_times.append(time.time())

    def library_item_for(self, tmdb_id: int,
                         media_type: str | MediaType | None = None) -> Optional[LibraryItem]:
        wanted_type = MediaType(media_type) if media_type else None
        for item in self.storage.get_library():
            if item.tmdb_id == tmdb_id and (wanted_type is None or item.media_type == wanted_type):
                return item
        return None

    def verified_placement_for(self, session: AgentSession) -> Optional[str]:
        """Return the library destination proven to come from this media session.

        The source-download marker is written by inventory_write only after its
        independent ffprobe/TMDB checks pass. This keeps transfer bookkeeping a
        consequence of verified reality rather than an agent's summary text.
        """
        if not session.job_id or not session.download_id:
            return None
        job = self.store.get_job(session.job_id)
        item = self.library_item_for(job.tmdb_id, job.media_type) if job else None
        if not item:
            return None
        if job.media_type == MediaType.MOVIE.value:
            if (item.metadata.get("verified") and
                    item.metadata.get("source_download_id") == session.download_id and
                    Path(item.path).is_file()):
                return item.path
            return None
        for season in item.episodes.values():
            for episode in season.values():
                if (episode.get("verified") and
                        episode.get("source_download_id") == session.download_id and
                        Path(episode.get("path") or "").is_file()):
                    return item.path
        return None

    async def mark_session_download_organized(self, session: AgentSession) -> bool:
        await self.refresh_missing_verified_quality(session)
        placement = self.verified_placement_for(session)
        download = self.storage.get_download(session.download_id)
        if not placement or not download:
            return False
        await self.storage.update_download(
            download.id,
            status=DownloadStatus.ORGANIZED,
            library_path=placement,
            completed_at=download.completed_at or time.time(),
        )
        refreshed = self.storage.get_download(download.id)
        await self.broadcast({
            "type": "download_update",
            "data": (refreshed or download).to_dict(),
        })
        return True

    async def refresh_missing_verified_quality(self, session: AgentSession) -> None:
        """Backfill old verified inventory from the file when its quality was lost."""
        if not session.job_id or not session.download_id:
            return
        job = self.store.get_job(session.job_id)
        item = self.library_item_for(job.tmdb_id, job.media_type) if job else None
        if not job or not item:
            return
        if job.media_type == MediaType.MOVIE.value:
            metadata = item.metadata
            if (metadata.get("verified") and
                    metadata.get("source_download_id") == session.download_id and
                    not quality_rank(metadata.get("quality", "")) and
                    Path(item.path).is_file()):
                facts = await _probe_media_facts(Path(item.path))
                metadata["quality"] = facts["quality"]
                await self.storage.update_library_item(item.id, metadata=metadata)
                await self.broadcast({"type": "library_update", "data": item.to_dict()})
            return
        changed = False
        for season in item.episodes.values():
            for episode in season.values():
                path = Path(episode.get("path") or "")
                if (episode.get("verified") and
                        episode.get("source_download_id") == session.download_id and
                        not quality_rank(episode.get("quality", "")) and path.is_file()):
                    episode["quality"] = (await _probe_media_facts(path))["quality"]
                    changed = True
        if changed:
            await self.storage.update_library_item(item.id, episodes=item.episodes)
            await self.broadcast({"type": "library_update", "data": item.to_dict()})

    def job_for(self, session: AgentSession):
        return self.store.get_job(session.job_id) if session.job_id else None

    async def enforce_mandate(self, tmdb_id: int, media_type: str,
                              wanted_episodes: dict, origin: str) -> None:
        """Refuse agent-originated work outside the user's recorded authority.

        Agents may reason their way to any conclusion; this check is what
        actually gates acquisition. Owning episodes is never authority to
        acquire more — only an explicit user request or monitoring grant is.
        """
        mandate = self.store.get_mandate(tmdb_id, media_type)
        if not mandate:
            raise ToolError(
                "Scope refused: the user has never requested this title, so there "
                "is no authority to acquire it. Files on disk are not a mandate. "
                "Journal it as a suggestion if you think the user would want it.")
        if media_type == "movie":
            return  # the movie itself was user-requested; re-grabs/upgrades ok
        air_dates: dict[int, dict[int, Optional[float]]] = {}
        violations: list[str] = []
        for season_key, episodes in (wanted_episodes or {}).items():
            season = int(season_key)
            season_airs: Optional[dict[int, Optional[float]]] = None
            if mandate.mode == MonitoringMode.KEEP_CURRENT:
                if season not in air_dates:
                    data = await self.tmdb_get(f"/tv/{tmdb_id}/season/{season}")
                    parsed: dict[int, Optional[float]] = {}
                    for entry in data.get("episodes", []):
                        raw = entry.get("air_date")
                        try:
                            aired = time.mktime(time.strptime(raw, "%Y-%m-%d")) if raw else None
                        except (TypeError, ValueError):
                            aired = None
                        parsed[int(entry.get("episode_number") or 0)] = aired
                    air_dates[season] = parsed
                season_airs = air_dates[season]
            for episode in episodes:
                aired_at = (season_airs or {}).get(int(episode))
                if not mandate.allows_episode(season, int(episode), aired_at):
                    violations.append(f"S{season:02d}E{int(episode):02d}")
        if violations:
            sample = ", ".join(violations[:10])
            suffix = f" and {len(violations) - 10} more" if len(violations) > 10 else ""
            raise ToolError(
                f"Scope refused: {sample}{suffix} exceed the user's mandate "
                f"({mandate.describe()}). Owning other episodes does not grant "
                "this. If you believe the user wants it, journal the suggestion — "
                "only the user can widen the scope in Preferences.")

    async def journal(self, ctx: ToolCtx, text: str, agent: str) -> JournalEntry:
        entry = JournalEntry(job_id=ctx.session.job_id, session_id=ctx.session.id,
                             agent=agent, text=text.strip())
        self.store.add_journal(entry)
        await self.broadcast({"type": "journal", "data": entry.to_dict()})
        return entry

    def staging_root(self, session: AgentSession) -> Path:
        """The staging directory this session may touch.

        Media sessions are confined to their own download's staging folder —
        one landed download must never be able to see or move another's
        files. Legacy downloads whose staging_path is the shared root fall
        back to it.
        """
        cfg = self.cfg()
        if not cfg.staging_dir:
            raise ToolError("Staging folder isn't configured.")
        base = Path(cfg.staging_dir).resolve()
        if session.agent == AgentKind.MEDIA and session.download_id:
            dl = self.storage.get_download(session.download_id)
            if dl and dl.staging_path:
                own = Path(dl.staging_path).resolve()
                if own != base and base in own.parents:
                    return own
        return base

    def jail_roots(self, session: AgentSession) -> list[Path]:
        cfg = self.cfg()
        roots = []
        if cfg.staging_dir:
            roots.append(self.staging_root(session))
        if cfg.library_dir:
            roots.append(Path(cfg.library_dir).resolve())
        if not roots:
            raise ToolError("Staging/library folders are not configured yet.")
        return roots

    def jailed(self, session: AgentSession, raw: str, *, must_exist: bool = False) -> Path:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            # relative paths resolve against this session's staging root
            p = self.staging_root(session) / p
        p = p.resolve()
        roots = self.jail_roots(session)
        if not any(p == r or r in p.parents for r in roots):
            raise ToolError(f"Path {p} is outside your allowed folders "
                            f"({', '.join(str(r) for r in roots)}).")
        if must_exist and not p.exists():
            raise ToolError(f"Path does not exist: {p}")
        return p

    def in_library(self, session: AgentSession, p: Path) -> bool:
        lib = self.cfg().library_dir
        if not lib:
            return False
        lib_path = Path(lib).resolve()
        return p == lib_path or lib_path in p.parents


def _t(name: str, description: str, properties: dict, required: list[str],
       handler) -> ToolDef:
    return ToolDef(name=name, description=description,
                   input_schema={"type": "object", "properties": properties,
                                 "required": required},
                   handler=handler)


# ─── Shared tools (all agents) ───────────────────────────────────────────────

def journal_tool(tb: Toolbox, agent: str) -> ToolDef:
    async def handler(ctx: ToolCtx, args: dict):
        text = (args.get("text") or "").strip()
        if not text:
            raise ToolError("Journal text is empty.")
        if len(text) > 360:
            raise ToolError(
                "Journal updates must be 360 characters or fewer. Rewrite this as at "
                "most two short sentences: what changed, then what happens next. "
                "Execution details are logged separately."
            )
        await tb.journal(ctx, text, agent)
        return "Noted in the journal."
    return _t("journal_write",
              "Write a concise user-facing progress update only when state changed. "
              "Maximum 360 characters and two short sentences: what changed, then "
              "what happens next. The execution log already captures detailed steps. "
              "Never include hashes, counts, codecs, or release names.",
              {"text": {"type": "string"}}, ["text"], handler)


def memory_tools(tb: Toolbox) -> list[ToolDef]:
    async def read(ctx: ToolCtx, args: dict):
        scope = args.get("scope", "show")
        job = tb.job_for(ctx.session)
        tmdb_id = args.get("tmdb_id") or (job.tmdb_id if job else None)
        content = tb.store.read_memory(scope, tmdb_id, user_id=job.user_id if job else ctx.session.user_id)
        return content or "(no notes yet)"

    async def write(ctx: ToolCtx, args: dict):
        scope = args.get("scope", "show")
        job = tb.job_for(ctx.session)
        tmdb_id = args.get("tmdb_id") or (job.tmdb_id if job else None)
        tb.store.write_memory(scope, args.get("content", ""), tmdb_id, user_id=job.user_id if job else ctx.session.user_id)
        return "Memory saved."

    return [
        _t("memory_read",
           "Read your notes. scope='show' for this show's playbook (which query "
           "phrasings worked, reliable/fake release groups), scope='global' for "
           "cross-show lessons.",
           {"scope": {"type": "string", "enum": ["show", "global"]},
            "tmdb_id": {"type": "integer", "description": "override show id (librarian)"}},
           ["scope"], read),
        _t("memory_write",
           "Replace your notes for a scope. Keep them short and useful — lessons, "
           "not a transcript. Read before writing so you don't clobber old lessons.",
           {"scope": {"type": "string", "enum": ["show", "global"]},
            "content": {"type": "string"},
            "tmdb_id": {"type": "integer"}},
           ["scope", "content"], write),
    ]


def wake_tool(tb: Toolbox) -> ToolDef:
    async def handler(ctx: ToolCtx, args: dict):
        minutes = args.get("minutes")
        at = args.get("at")
        reason = (args.get("reason") or "").strip()
        if not reason:
            raise ToolError("Give a plain-language reason — the user sees it on the card.")
        if at:
            try:
                wake_ts = time.mktime(time.strptime(at, "%Y-%m-%d %H:%M"))
            except ValueError:
                raise ToolError("Use 'YYYY-MM-DD HH:MM' local time for `at`.")
        elif minutes:
            wake_ts = time.time() + float(minutes) * 60
        else:
            wake_ts = 0.0  # event-only hibernation
        ctx.session.wake_at = wake_ts
        ctx.session.wake_reason = reason
        ctx.hibernate = True
        job = tb.job_for(ctx.session)
        if job:
            job.state_line = reason
            job.next_wake_at = wake_ts
            tb.store.save_job(job)
            await tb.broadcast({"type": "job_update", "data": job.to_dict()})
        if wake_ts:
            return f"Hibernating until {time.strftime('%Y-%m-%d %H:%M', time.localtime(wake_ts))} (or an earlier event)."
        return "Hibernating until the next event."
    return _t("wake_me",
              "End this turn and hibernate. Provide `minutes` OR `at` ('YYYY-MM-DD HH:MM' "
              "local) for a timed wake, or neither to sleep until an event (download "
              "finishes, stalls, etc). `reason` is shown to the user in plain language, "
              "e.g. \"Episodes 9–10 haven't aired — checking again Friday night.\"",
              {"minutes": {"type": "number"}, "at": {"type": "string"},
               "reason": {"type": "string"}},
              ["reason"], handler)


# ─── TMDB tools ──────────────────────────────────────────────────────────────

def tmdb_tools(tb: Toolbox) -> list[ToolDef]:
    async def search(ctx: ToolCtx, args: dict):
        media = args.get("media_type", "tv")
        data = await tb.tmdb_get(f"/search/{media}", query=args["query"])
        out = []
        for r in (data.get("results") or [])[:8]:
            out.append({"tmdb_id": r["id"],
                        "title": r.get("name") or r.get("title"),
                        "year": (r.get("first_air_date") or r.get("release_date") or "")[:4],
                        "overview": (r.get("overview") or "")[:200]})
        return out or "No TMDB matches."

    async def show(ctx: ToolCtx, args: dict):
        tmdb_id = int(args["tmdb_id"])
        data = await tb.tmdb_get(f"/tv/{tmdb_id}")
        return {
            "tmdb_id": tmdb_id, "name": data.get("name"),
            "original_name": data.get("original_name"),
            "first_air_date": data.get("first_air_date"),
            "status": data.get("status"),
            "in_production": data.get("in_production"),
            "episode_run_time": data.get("episode_run_time"),
            "number_of_seasons": data.get("number_of_seasons"),
            "number_of_episodes": data.get("number_of_episodes"),
            "seasons": [
                {"season": s.get("season_number"), "episodes": s.get("episode_count"),
                 "air_date": s.get("air_date"), "name": s.get("name")}
                for s in data.get("seasons", []) if s.get("season_number", 0) > 0
            ],
            "next_episode_to_air": data.get("next_episode_to_air"),
            "last_episode_to_air": data.get("last_episode_to_air"),
        }

    async def movie(ctx: ToolCtx, args: dict):
        tmdb_id = int(args["tmdb_id"])
        data = await tb.tmdb_get(f"/movie/{tmdb_id}")
        return {
            "tmdb_id": tmdb_id,
            "title": data.get("title"),
            "original_title": data.get("original_title"),
            "release_date": data.get("release_date"),
            "runtime_minutes": data.get("runtime"),
            "status": data.get("status"),
        }

    async def season(ctx: ToolCtx, args: dict):
        tmdb_id, season_n = int(args["tmdb_id"]), int(args["season"])
        data = await tb.tmdb_get(f"/tv/{tmdb_id}/season/{season_n}")
        return [{"episode": e.get("episode_number"), "name": e.get("name"),
                 "air_date": e.get("air_date"), "runtime_minutes": e.get("runtime")}
                for e in data.get("episodes", [])]

    async def titles(ctx: ToolCtx, args: dict):
        tmdb_id = int(args["tmdb_id"])
        media = args.get("media_type", "tv")
        data = await tb.tmdb_get(f"/{media}/{tmdb_id}/alternative_titles")
        alts = data.get("results") or data.get("titles") or []
        details = await tb.tmdb_get(f"/{media}/{tmdb_id}")
        return {"original_title": details.get("original_name") or details.get("original_title"),
                "alternative_titles": [
                    {"title": a.get("title"), "country": a.get("iso_3166_1")}
                    for a in alts][:25]}

    return [
        _t("tmdb_search", "Search TMDB for a show or movie by title.",
           {"query": {"type": "string"},
            "media_type": {"type": "string", "enum": ["tv", "movie"]}},
           ["query"], search),
        _t("tmdb_show",
           "TV show facts from TMDB: seasons, episode counts, air dates, typical "
           "runtimes, whether it's still airing. This is ground truth for what exists.",
           {"tmdb_id": {"type": "integer"}}, ["tmdb_id"], show),
        _t("tmdb_movie",
           "Movie facts from TMDB: canonical title, release date, and runtime. "
           "Use runtime to verify the landed file.",
           {"tmdb_id": {"type": "integer"}}, ["tmdb_id"], movie),
        _t("tmdb_season",
           "Episode list for one season: numbers, titles, air dates, per-episode "
           "runtimes in minutes. Use runtimes to verify files are what they claim.",
           {"tmdb_id": {"type": "integer"}, "season": {"type": "integer"}},
           ["tmdb_id", "season"], season),
        _t("tmdb_titles",
           "Original and alternative titles (other languages, romanizations, AKA "
           "titles) — useful for refining searches that miss.",
           {"tmdb_id": {"type": "integer"},
            "media_type": {"type": "string", "enum": ["tv", "movie"]}},
           ["tmdb_id"], titles),
    ]


# ─── Indexer + torrent client tools (Fetch Agent) ────────────────────────────

def fetch_tools(tb: Toolbox) -> list[ToolDef]:
    async def search(ctx: ToolCtx, args: dict):
        await tb.rate_limit_search()
        ctx.session.spend.searches += 1
        if tb.cfg().preferred_search_engines != ['apibay']:
            raise ToolError('The configured acquisition source is not installed. Choose the built-in source in Server settings.')
        try:raw = await apibay_query(args["query"],strict=True)
        except Exception as exc:
            raise ToolError('The acquisition source is unavailable. This is not an empty search result; wait for the source to recover before trying more queries.') from exc
        out = []
        for r in raw:
            try:
                size = int(r.get("size", 0))
            except (TypeError, ValueError):
                size = 0
            out.append({
                "apibay_id": r.get("id"),
                "name": r.get("name"),
                "info_hash": r.get("info_hash"),
                "seeders": int(r.get("seeders", 0)),
                "leechers": int(r.get("leechers", 0)),
                "size_gb": round(size / 1e9, 2),
                "num_files": int(r.get("num_files", 0) or 0),
                "uploaded": time.strftime("%Y-%m-%d", time.localtime(int(r.get("added", 0) or 0))),
                "uploader": r.get("username", ""),
            })
        if not out:
            return "No results for this query. Names lie and searches are literal — try alt titles, other phrasings, per-episode probes."
        return out

    async def peek(ctx: ToolCtx, args: dict):
        ctx.session.spend.peeks += 1
        apibay_id = str(args["apibay_id"])
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"https://apibay.org/f.php?id={urllib.parse.quote(apibay_id)}")
                data = r.json()
        except Exception as e:
            raise ToolError(f"File listing unavailable ({e}). Weigh grab-inspect-abandon instead.")
        files = []
        for entry in data if isinstance(data, list) else []:
            if not isinstance(entry, dict):
                continue
            name = str(_apibay_indexed_value(entry.get("name"), "") or "")
            try:
                size = int(_apibay_indexed_value(entry.get("size"), 0) or 0)
            except (TypeError, ValueError):
                size = 0
            if name and name != "Filelist not found":
                files.append({"file": name, "size_mb": round(size / 1e6, 1)})
        if not files:
            return ("No file listing available for this torrent (the indexer doesn't "
                    "have it). If the swarm is healthy you can grab it, inspect what "
                    "arrives, and abandon it if it's wrong.")
        return files

    async def add(ctx: ToolCtx, args: dict):
        # Reserve durably before external work. The shared lock makes the cap
        # atomic across sessions and lets pause/cancel reconcile an in-flight add.
        async with tb.transfer_lock:
            job = tb.require_authority(ctx)
            cfg = tb.cfg()
            if not cfg.staging_dir:
                raise ToolError("Staging folder isn't configured.")
            info_hash = (args.get("info_hash") or "").strip().lower()
            if len(info_hash) != 40 or any(c not in "0123456789abcdef" for c in info_hash):
                raise ToolError("info_hash must be a 40-character hexadecimal hash.")
            name = args.get("name") or info_hash
            download_id = f"dl-{info_hash}"
            existing = tb.storage.get_download(download_id)
            if existing and existing.status != DownloadStatus.ERROR:
                if existing.metadata.get("job_id") != job.id:
                    raise ToolError("This transfer already belongs to another request.")
                return {"download_id": existing.id, "note": "This transfer is already recorded."}
            limit = max(1, int(cfg.max_active_transfers or 1))
            active = [d for d in tb.storage.get_all_downloads()
                      if d.metadata.get("agent_managed") and d.status in
                      (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED)]
            if len(active) >= limit:
                tb.wait_for_slot(job.id)
                raise ToolError(f"Transfer limit reached: {len(active)} of {limit} slots reserved. "
                                "You're queued and will be woken as soon as a slot opens: "
                                "hibernate without a timer.")
            mgr, connected, recovery = await tb.connect_torrents()
            tb.require_authority(ctx)
            if not connected:
                raise ToolError(f"{recovery} Wait for the download app to recover; keep this candidate.")
            staging = Path(cfg.staging_dir).resolve() / download_id
            staging.mkdir(parents=True, exist_ok=True)
            magnet = build_magnet(info_hash, name)
            wanted_files = [str(f) for f in (args.get("files") or []) if str(f).strip()][:200]
            dl = Download(id=download_id, name=name, magnet_url=magnet,
                media_type=MediaType(job.media_type), status=DownloadStatus.QUEUED,
                torrent_hash=info_hash, staging_path=str(staging), tmdb_id=job.tmdb_id,
                metadata={"job_id": job.id, "session_id": ctx.session.id,
                          "job_revision": job.revision, "agent_managed": True,
                          **({"wanted_files": wanted_files} if wanted_files else {})})
            await tb.storage.add_download(dl)
            try:
                torrent_hash = await mgr.add_magnet(magnet, str(staging))
            except Exception as exc:
                # The client may have accepted it before the connection broke.
                # Keep the hash/receipt for reconciliation rather than add twice.
                await tb.storage.update_download(dl.id, error_message="Confirming download-app response.")
                raise ToolError("Download result is uncertain; checking the recorded transfer before retrying.") from exc
            await tb.storage.update_download(dl.id, status=DownloadStatus.DOWNLOADING,
                                             torrent_hash=torrent_hash or info_hash)
            await tb.broadcast({"type": "download_added", "data": dl.to_dict()})
            tb.slot_taken(job.id)
            if wanted_files:
                asyncio.create_task(tb.select_soon(dl.id))
            return {"download_id": dl.id, "hash": dl.torrent_hash,
                    "note": "Added. Progress and completion will wake this request."
                    + (" Only the chosen files will download once the file list arrives." if wanted_files else "")}

    async def status(ctx: ToolCtx, args: dict):
        mgr, connected, recovery = await tb.connect_torrents()
        if not connected:
            return (f"{recovery} This is a download-app problem, not a torrent problem. "
                    "Write one short update and hibernate; you'll be woken when it recovers.")
        out = []
        for dl in tb.storage.get_all_downloads():
            if dl.metadata.get("job_id") != ctx.session.job_id:
                continue
            if dl.status in (DownloadStatus.ORGANIZED, DownloadStatus.ERROR) and not args.get("include_done"):
                continue
            st = await mgr.get_torrent_status(dl.torrent_hash)
            raw_state = st.get("status") if st else None
            state = getattr(raw_state, "value", raw_state) if st else f"{dl.status.value} (not in client)"
            out.append({
                "download_id": dl.id, "name": dl.name,
                "state": state,
                "progress_pct": round((st.get("progress", dl.progress) if st else dl.progress) * 100, 1),
                "speed_mbps": round((st.get("download_speed", 0) if st else 0) / 1e6, 2),
                "eta_minutes": round(st["eta_seconds"] / 60) if st and st.get("eta_seconds", -1) >= 0 else None,
            })
        return out or "No active downloads for this job."

    async def remove(ctx: ToolCtx, args: dict):
        dl = tb.storage.get_download(args["download_id"])
        if not dl or dl.metadata.get("job_id") != ctx.session.job_id:
            raise ToolError("No such download on this job.")
        mgr, connected, _ = await tb.connect_torrents()
        delete_files = bool(args.get("delete_files", True))
        if connected:
            await mgr.delete_torrent(dl.torrent_hash, delete_files=delete_files)
        if delete_files:
            remove_download_staging(tb.cfg().staging_dir, dl)
        await tb.storage.update_download(dl.id, status=DownloadStatus.ERROR,
                                         error_message=args.get("reason", "removed by agent"))
        await tb.broadcast({"type": "download_update", "data": dl.to_dict()})
        return "Removed."

    async def triage(ctx: ToolCtx, args: dict):
        names = args.get("names") or []
        out = []
        for n in names[:100]:
            p = parse_release_name(n)
            out.append({"name": n, "guess": {
                "seasons": p.seasons, "episodes": p.episodes,
                "season_pack": p.is_season_pack, "complete_series": p.is_complete_series,
                "quality": p.quality, "source": p.source, "codec": p.codec,
                "group": p.group, "confidence": p.confidence, "risks": p.risk_flags}})
        return {"advisory": "Regex guesses only — names lie. Verify anything "
                            "consequential with torrent_peek or by inspecting files.",
                "parses": out}

    async def inventory(ctx: ToolCtx, args: dict):
        job = tb.job_for(ctx.session)
        tmdb_id = args.get("tmdb_id") or (job.tmdb_id if job else None)
        item = tb.library_item_for(
            tmdb_id, job.media_type if job else None) if tmdb_id else None
        if not item:
            return "Nothing in the library for this title yet."
        if item.media_type == MediaType.MOVIE:
            return {"title": item.title, "path": item.path, "media_type": "movie",
                    "verified": bool(item.metadata.get("verified")),
                    "duration_minutes": item.metadata.get("duration_minutes"),
                    "quality": item.metadata.get("quality", "unknown"),
                    "size_bytes": item.size_bytes}
        return {"title": item.title, "path": item.path, "media_type": "tv",
                "episodes": item.episodes,
                "note": "episodes is {season: {episode: {quality, path, size_bytes, verified}}}"}

    async def close(ctx: ToolCtx, args: dict):
        job = tb.require_authority(ctx)
        if not job:
            raise ToolError("No job attached to this session.")
        outcome = args["outcome"]
        if outcome == "complete":
            item = tb.library_item_for(job.tmdb_id, job.media_type)
            if not item:
                raise ToolError("Completion refused: there is no library inventory for this job.")
            if job.media_type == "movie":
                if item.media_type != MediaType.MOVIE or media_state(item.metadata, item.path) != "ready":
                    raise ToolError("Completion refused: the movie is not verified in library inventory.")
                if not audio_satisfies(item.metadata, job.audio_pref, job.original_language):
                    raise ToolError("Completion refused: verified audio does not satisfy this request.")
                if quality_rank(item.metadata.get("quality", "")) < quality_rank(job.min_quality):
                    raise ToolError("Completion refused: the verified movie is below the job's minimum quality.")
            else:
                missing = []
                for season, episodes in job.wanted_episodes.items():
                    for episode in episodes:
                        record = item.episode_file(int(season), int(episode))
                        if (not record or media_state(record) != "ready" or
                                not audio_satisfies(record, job.audio_pref, job.original_language) or
                                quality_rank(record.get("quality", "")) < quality_rank(job.min_quality)):
                            missing.append(f"S{int(season):02d}E{int(episode):02d}")
                if missing:
                    sample = ", ".join(missing[:12])
                    suffix = f" and {len(missing) - 12} more" if len(missing) > 12 else ""
                    raise ToolError(f"Completion refused: verified inventory still misses {sample}{suffix}.")
        job.status = JobStatus.COMPLETE if outcome == "complete" else JobStatus.ABANDONED
        job.state_line = args.get("note", "")
        job.next_wake_at = 0.0
        job.closed_at = time.time()
        tb.store.save_job(job)
        ctx.close = True
        ctx.close_reason = f"{outcome}: {args.get('note', '')}"
        await tb.broadcast({"type": "job_update", "data": job.to_dict()})
        return f"Job closed as {outcome}."

    return [
        _t("tpb_search",
           "Raw indexer search — unfiltered results, newest metadata the indexer has. "
           "Searches are literal substring-ish matches: refine with alt titles, "
           "romanizations, tag variants (S01, Season 1, COMPLETE), per-episode probes.",
           {"query": {"type": "string"}}, ["query"], search),
        _t("torrent_peek",
           "Fetch a torrent's ACTUAL file listing before committing (names lie; file "
           "lists don't). Costs a lookup — on marginal swarms it may be unavailable, "
           "in which case grab-inspect-abandon is the alternative.",
           {"apibay_id": {"type": "string"}}, ["apibay_id"], peek),
        _t("client_add",
           "Add a torrent to the download client (staging folder). Returns a "
           "download_id. You'll be woken on completion, stall, or error. From a "
           "pack, pass files: the names torrent_peek listed for the wanted "
           "episodes, and only those download.",
           {"info_hash": {"type": "string"}, "name": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}}},
           ["info_hash", "name"], add),
        _t("client_status", "Live status of this job's downloads.",
           {"include_done": {"type": "boolean"}}, [], status),
        _t("client_remove",
           "Remove a download (e.g. stalled, or contents turned out wrong). "
           "delete_files defaults true.",
           {"download_id": {"type": "string"}, "delete_files": {"type": "boolean"},
            "reason": {"type": "string"}}, ["download_id"], remove),
        _t("triage_parse",
           "Cheap regex triage over many release names at once (seasons/episodes/"
           "quality guesses). ADVISORY ONLY — you decide, and you verify.",
           {"names": {"type": "array", "items": {"type": "string"}}}, ["names"], triage),
        _t("inventory_read",
           "What the library actually holds for this show right now — the ground "
           "truth your job spec is reconciled against.",
           {"tmdb_id": {"type": "integer"}}, [], inventory),
        _t("job_close",
           "Close the job. 'complete' ONLY when inventory provably matches the spec "
           "(verified files for every wanted episode within the quality window). "
           "'abandoned' when you've concluded the content genuinely isn't out there — "
           "explain in the note.",
           {"outcome": {"type": "string", "enum": ["complete", "abandoned"]},
            "note": {"type": "string"}}, ["outcome"], close),
    ]


# ─── Filesystem + inventory tools (Media Agent) ──────────────────────────────

def media_tools(tb: Toolbox) -> list[ToolDef]:
    async def fs_list(ctx: ToolCtx, args: dict):
        p = tb.jailed(ctx.session, args.get("path") or str(tb.staging_root(ctx.session)),
                      must_exist=True)
        if p.is_file():
            return [{"path": str(p), "size_mb": round(p.stat().st_size / 1e6, 1)}]
        out = []
        for f in sorted(p.rglob("*")):
            if f.is_file():
                out.append({"path": str(f), "size_mb": round(f.stat().st_size / 1e6, 1)})
            if len(out) >= 500:
                break
        return out or "(empty)"

    async def fs_probe(ctx: ToolCtx, args: dict):
        p = tb.jailed(ctx.session, args["path"], must_exist=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(p),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except FileNotFoundError:
            raise ToolError("ffprobe is not installed on this machine — judge by size "
                            "and extension instead, and note the limitation.")
        except asyncio.TimeoutError:
            raise ToolError("ffprobe timed out — the file may be corrupt or still writing.")
        if proc.returncode != 0 or not stdout:
            return {"path": str(p), "probe": "failed — not a readable media file "
                                             "(corrupt, incomplete, or junk)"}
        data = json.loads(stdout)
        fmt = data.get("format", {})
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
        return {
            "path": str(p),
            "duration_minutes": round(float(fmt.get("duration", 0)) / 60, 1),
            "size_mb": round(int(fmt.get("size", 0) or 0) / 1e6, 1),
            "video": {"codec": video.get("codec_name"),
                      "width": video.get("width"), "height": video.get("height")},
            "audio": [{"codec": a.get("codec_name"),
                       "language": (a.get("tags") or {}).get("language", "und"),
                       "channels": a.get("channels")} for a in audio],
        }

    async def fs_move(ctx: ToolCtx, args: dict):
        src = tb.jailed(ctx.session, args["src"], must_exist=True)
        dst = tb.jailed(ctx.session, args["dst"])
        if tb.in_library(ctx.session, src):
            raise ToolError("Existing library files cannot be moved with fs_move. "
                            "Verified replacements must use upgrade_swap.")
        if dst.exists():
            raise ToolError(f"Destination already exists: {dst}. Overwriting library "
                            "files is only allowed via upgrade_swap.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved to {dst}"

    async def fs_delete(ctx: ToolCtx, args: dict):
        p = tb.jailed(ctx.session, args["path"], must_exist=True)
        if tb.in_library(ctx.session, p):
            raise ToolError("Deleting inside the library is forbidden — the only "
                            "permitted library deletion is a verified upgrade_swap.")
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return f"Deleted {p} (staging only)."

    async def upgrade_swap(ctx: ToolCtx, args: dict):
        old = tb.jailed(ctx.session, args["old_path"], must_exist=True)
        new = tb.jailed(ctx.session, args["new_path"], must_exist=True)
        if not tb.in_library(ctx.session, old):
            raise ToolError("old_path must be the existing library file.")
        if tb.in_library(ctx.session, new):
            raise ToolError("new_path must be the incoming file in staging.")
        if new.suffix.lower() not in VIDEO_EXT or old.suffix.lower() not in VIDEO_EXT:
            raise ToolError("Swap is for video files only.")
        old_duration, new_duration = await asyncio.gather(
            _probe_duration_seconds(old), _probe_duration_seconds(new))
        if abs(old_duration - new_duration) > 120:
            raise ToolError(
                "Replacement refused: ffprobe durations differ by more than two minutes "
                f"({old_duration / 60:.1f} vs {new_duration / 60:.1f})."
            )
        target = old.parent / (args.get("new_name") or new.name)
        if target != old and target.exists():
            raise ToolError(f"Replacement destination already exists: {target}")

        token = uuid.uuid4().hex
        incoming = old.parent / f".sparrow-incoming-{token}{new.suffix}"
        backup = old.parent / f".sparrow-backup-{token}{old.suffix}"
        shutil.move(str(new), str(incoming))
        try:
            old.rename(backup)
            incoming.rename(target)
        except Exception:
            if backup.exists() and not old.exists():
                backup.rename(old)
            if incoming.exists() and not new.exists():
                shutil.move(str(incoming), str(new))
            raise
        backup.unlink()
        return f"Swapped in {target} (old file removed)."

    async def inv_write(ctx: ToolCtx, args: dict):
        tmdb_id = int(args["tmdb_id"])
        job = tb.job_for(ctx.session)
        media_type = MediaType((args.get("media_type") or
                                (job.media_type if job else "tv")))
        path = tb.jailed(ctx.session, args["path"], must_exist=True)
        verified = bool(args.get("verified", False))
        facts = {}
        actual_duration = None
        actual_quality = args.get("quality", "unknown")
        if verified:
            facts = await _probe_media_facts(path)
            actual_duration = facts["duration_seconds"] / 60
            actual_quality = facts["quality"]

        if media_type == MediaType.MOVIE:
            details = await tb.tmdb_get(f"/movie/{tmdb_id}")
            expected_runtime = float(details.get("runtime") or 0)
            if verified and (not expected_runtime or abs(actual_duration - expected_runtime) > 2):
                raise ToolError(
                    "Movie verification refused: ffprobe duration does not match TMDB runtime."
                )
            item = tb.library_item_for(tmdb_id, MediaType.MOVIE)
            if not item:
                year_text = (details.get("release_date") or "")[:4]
                item = LibraryItem(
                    id=f"movie-{tmdb_id}",
                    title=details.get("title") or f"movie-{tmdb_id}",
                    media_type=MediaType.MOVIE,
                    path=str(path),
                    tmdb_id=tmdb_id,
                    year=int(year_text) if year_text.isdigit() else None,
                    overview=details.get("overview", ""),
                )
                await tb.storage.add_library_item(item)
            item.path = str(path)
            item.size_bytes = path.stat().st_size
            item.metadata.update({
                "verified": verified,
                "file_version": file_version(path),
                "audio_languages": facts.get("audio_languages", []),
                "audio_tracks": facts.get("audio_tracks", []),
                "duration_minutes": round(actual_duration, 2) if actual_duration else None,
                "quality": actual_quality,
                "added_at": time.time(),
                "source_download_id": ctx.session.download_id if verified else "",
            })
            await tb.storage.update_library_item(
                item.id, path=item.path, size_bytes=item.size_bytes, metadata=item.metadata)
            await tb.broadcast({"type": "library_update", "data": item.to_dict()})
            return f"Inventory updated: verified movie recorded for TMDB {tmdb_id}."

        if args.get("season") is None or args.get("episode") is None:
            raise ToolError("TV inventory requires season and episode numbers.")
        item = tb.library_item_for(tmdb_id, MediaType.TV)
        if not item:
            details = await tb.tmdb_get(f"/tv/{tmdb_id}")
            cfg = tb.cfg()
            item = LibraryItem(
                id=f"tv-{tmdb_id}", title=details.get("name") or f"tv-{tmdb_id}",
                media_type=MediaType.TV,
                path=args.get("show_path") or str(Path(cfg.library_dir) / (details.get("name") or str(tmdb_id))),
                tmdb_id=tmdb_id,
                year=int((details.get("first_air_date") or "0000")[:4] or 0) or None,
                overview=details.get("overview", ""),
                seasons=details.get("number_of_seasons"),
                episode_count=details.get("number_of_episodes"),
            )
            await tb.storage.add_library_item(item)
        season, episode = int(args["season"]), int(args["episode"])
        if verified:
            season_data = await tb.tmdb_get(f"/tv/{tmdb_id}/season/{season}")
            episode_data = next(
                (entry for entry in season_data.get("episodes", [])
                 if int(entry.get("episode_number") or 0) == episode), None)
            expected_runtime = float((episode_data or {}).get("runtime") or 0)
            if not expected_runtime:
                show_data = await tb.tmdb_get(f"/tv/{tmdb_id}")
                runtimes = show_data.get("episode_run_time") or []
                expected_runtime = float(runtimes[0]) if runtimes else 0
            if not expected_runtime or abs(actual_duration - expected_runtime) > 2:
                raise ToolError(
                    "Episode verification refused: ffprobe duration does not match TMDB runtime."
                )
        item.set_episode_file(season, episode, {
            "quality": actual_quality,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "added_at": time.time(),
            "verified": verified,
            "file_version": file_version(path),
            "audio_languages": facts.get("audio_languages", []),
            "audio_tracks": facts.get("audio_tracks", []),
            "duration_minutes": round(actual_duration, 2) if actual_duration else None,
            "source_download_id": ctx.session.download_id if verified else "",
        })
        await tb.storage.update_library_item(item.id, episodes=item.episodes)
        await tb.broadcast({"type": "library_update", "data": item.to_dict()})
        return f"Inventory updated: S{season:02d}E{episode:02d} recorded."

    async def report(ctx: ToolCtx, args: dict):
        text = (args.get("text") or "").strip()
        if not text:
            raise ToolError("Say what you found — the Fetch Agent acts on this.")
        job_id = ctx.session.job_id
        await tb.emit(Event(kind="media_report", job_id=job_id,
                            download_id=ctx.session.download_id,
                            payload={"description": text}))
        return "Reported to the Fetch Agent."

    async def escalate(ctx: ToolCtx, args: dict):
        smart = tb.smart_model()
        if ctx.session.model == smart:
            return "Already on the smart tier."
        ctx.session.model = smart
        return (f"Escalated to {smart} for the rest of this session. "
                f"Reason noted: {args.get('reason', '')}")

    async def done(ctx: ToolCtx, args: dict):
        await tb.mark_session_download_organized(ctx.session)
        ctx.close = True
        ctx.close_reason = args.get("summary", "done")
        return "Session closed."

    return [
        _t("fs_list", "List files (recursively) in staging or the library. Paths are "
                      "jailed to those folders.",
           {"path": {"type": "string"}}, [], fs_list),
        _t("fs_probe",
           "ffprobe a file: real duration, resolution, codecs, audio languages. This "
           "is ground truth — a 23-minute file cannot be a 58-minute episode, and a "
           "40 MB '1080p' is a fake.",
           {"path": {"type": "string"}}, ["path"], fs_probe),
        _t("fs_move",
           "Move/rename a file into place (creates folders). Refuses to overwrite — "
           "upgrades go through upgrade_swap.",
           {"src": {"type": "string"}, "dst": {"type": "string"}}, ["src", "dst"], fs_move),
        _t("fs_delete",
           "Delete junk in STAGING only (samples, .exe fakes, leftover folders). "
           "Library deletions are impossible here by design.",
           {"path": {"type": "string"}}, ["path"], fs_delete),
        _t("upgrade_swap",
           "The one sanctioned library deletion: replace an existing library episode "
           "with a verified better copy. Probe both files first; durations must agree.",
           {"old_path": {"type": "string"}, "new_path": {"type": "string"},
            "new_name": {"type": "string"}},
           ["old_path", "new_path"], upgrade_swap),
        _t("inventory_write",
           "Record a placed movie or episode in library inventory. When verified=true, "
           "the tool independently ffprobes the file and compares its duration to TMDB. "
           "Movies need media_type='movie'; TV also needs season and episode.",
           {"tmdb_id": {"type": "integer"}, "season": {"type": "integer"},
            "episode": {"type": "integer"}, "path": {"type": "string"},
            "media_type": {"type": "string", "enum": ["movie", "tv"]},
            "quality": {"type": "string"}, "verified": {"type": "boolean"},
            "duration_minutes": {"type": "number"}, "show_path": {"type": "string"}},
           ["tmdb_id", "season", "episode", "path"], inv_write),
        _t("report_to_fetch",
           "Tell the Fetch Agent what actually arrived vs. what the pack claimed "
           "(e.g. 'claimed E01–E10 but E07 is a corrupt sample — you are not done'). "
           "This closes the loop that makes the job contract real.",
           {"text": {"type": "string"}}, ["text"], report),
        _t("escalate_model",
           "Switch yourself to the smart model tier when genuinely confused "
           "(ambiguous numbering, conflicting evidence). Costs more — use judgment.",
           {"reason": {"type": "string"}}, ["reason"], escalate),
        _t("session_done",
           "Close this session when every landed file is probed, placed, recorded — "
           "and you've reported the outcome to the Fetch Agent.",
           {"summary": {"type": "string"}}, ["summary"], done),
    ]


# ─── Librarian tools ─────────────────────────────────────────────────────────

def librarian_tools(tb: Toolbox, create_job) -> list[ToolDef]:
    """create_job: async (tmdb_id, wanted_episodes, origin, **knobs) -> Job"""

    async def overview(ctx: ToolCtx, args: dict):
        cfg = tb.cfg()
        preferred = cfg.quality_preference.value
        shows = []
        for item in tb.storage.get_library(MediaType.TV):
            mandate = tb.store.get_mandate(item.tmdb_id, "tv") if item.tmdb_id else None
            shows.append({
                "tmdb_id": item.tmdb_id, "title": item.title,
                "seasons_on_disk": {
                    s: {"have": info["have"], "lowest_quality": info["lowest_quality"]}
                    for s, info in item.season_summary().items()},
                "missing_artwork": not item.poster_path,
                "mandate": mandate.describe() if mandate else
                           "No user request on record — you cannot acquire anything.",
            })
        active = [{"job_id": j.id, "tmdb_id": j.tmdb_id, "title": j.title,
                   "state": j.state_line} for j in tb.store.get_jobs(JobStatus.ACTIVE)]
        return {"preferred_quality": preferred, "shows": shows, "active_jobs": active}

    async def spawn(ctx: ToolCtx, args: dict):
        tmdb_id = int(args["tmdb_id"])
        wanted = args.get("wanted_episodes") or {}
        if not wanted:
            raise ToolError("Name the exact episodes, e.g. {\"4\": [11]}. Blanket "
                            "requests are user decisions, not yours.")
        existing = [
            j for j in tb.store.get_jobs(JobStatus.ACTIVE)
            if j.tmdb_id == tmdb_id and j.media_type == MediaType.TV.value
        ]
        if existing:
            raise ToolError(f"There is already an active job for this show ({existing[0].id}). "
                            "One owner per show — don't double up.")
        job = await create_job(
            tmdb_id=tmdb_id,
            wanted_episodes=wanted,
            origin=args.get("origin", "librarian"),
            urgency=args.get("urgency", "whenever"),
        )
        return {"job_id": job.id, "note": "Fetch Agent session started."}

    return [
        _t("library_overview",
           "Everything on disk: per-show, per-season counts and lowest quality, the "
           "user's mandate (your acquisition authority) per show, plus currently "
           "active jobs (never spawn a duplicate).",
           {}, [], overview),
        _t("spawn_job",
           "Create a job (a Fetch Agent takes it from here). wanted_episodes maps "
           "season to episode numbers, e.g. {\"4\": [11]} for one new episode, or "
           "episodes below preferred quality for an upgrade job (origin='upgrade'). "
           "The tool refuses any episode outside the user's recorded mandate — "
           "owning a show is not permission to extend it.",
           {"tmdb_id": {"type": "integer"},
            "wanted_episodes": {"type": "object"},
            "origin": {"type": "string", "enum": ["librarian", "upgrade"]},
            "urgency": {"type": "string", "enum": ["tonight", "soon", "whenever"]}},
           ["tmdb_id", "wanted_episodes"], spawn),
    ]
