"""
Sparrow backend — FastAPI application.

Serves the REST API on port 8888 and the built React frontend as static files.
WebSocket at /ws broadcasts real-time download progress updates.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

# ─── Logging setup ────────────────────────────────────────────────────────────

import sys
import logging
import logging.handlers as _lh

_ANSI = {
    "reset":   "\033[0m",
    "grey":    "\033[90m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "red":     "\033[31m",
    "magenta": "\033[35m",
    "cyan":    "\033[36m",
    "bold":    "\033[1m",
}

class _ColorConsoleFormatter(logging.Formatter):
    """Human-readable, colour-coded console output for the sparrow.* namespace."""
    _LEVEL_COLOR = {
        "DEBUG":    _ANSI["grey"],
        "INFO":     _ANSI["green"],
        "WARNING":  _ANSI["yellow"],
        "ERROR":    _ANSI["red"],
        "CRITICAL": _ANSI["magenta"],
    }
    _use_color = sys.stderr.isatty() or sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        ts    = self.formatTime(record, "%H:%M:%S")
        level = record.levelname
        name  = record.name.replace("sparrow.", "")
        msg   = record.getMessage()

        # Collect extra fields (skip standard attrs)
        skip = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "funcName", "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "exc_info", "exc_text", "stack_info",
            "taskName",
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in skip and not k.startswith("_")}
        extra_str = "  " + "  ".join(f"{k}={v}" for k, v in extras.items()) if extras else ""
        if record.exc_info:
            extra_str += "\n" + self.formatException(record.exc_info)

        if not self._use_color:
            return f"{ts}  {level:<8}  {name:<20}  {msg}{extra_str}"

        c = self._LEVEL_COLOR.get(level, "")
        r = _ANSI["reset"]
        return (
            f"{_ANSI['grey']}{ts}{r}  "
            f"{c}{level:<8}{r}  "
            f"{_ANSI['cyan']}{name:<20}{r}  "
            f"{_ANSI['bold']}{msg}{r}"
            f"{_ANSI['grey']}{extra_str}{r}"
        )


class _JsonLogFormatter(logging.Formatter):
    """One JSON object per line — easy to parse in the frontend."""
    _SKIP = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "exc_info", "exc_text", "stack_info", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        d: dict = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in self._SKIP and not k.startswith("_"):
                try:
                    json.dumps(v)
                    d[k] = v
                except Exception:
                    d[k] = str(v)
        return json.dumps(d, ensure_ascii=False)


def _setup_logging(data_dir: str) -> None:
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    log_file = str(log_dir / "sparrow.log")

    # Always ensure our file handler is present — uvicorn may have already set up
    # its own handlers before lifespan runs, which would cause the old "if root.handlers"
    # guard to bail early and never attach the RotatingFileHandler.
    already_have_file = any(
        isinstance(h, _lh.RotatingFileHandler) and getattr(h, "baseFilename", None) == log_file
        for h in root.handlers
    )
    if already_have_file:
        return

    root.setLevel(logging.INFO)

    fh = _lh.RotatingFileHandler(
        str(log_dir / "sparrow.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(_JsonLogFormatter())
    root.addHandler(fh)

    # Console handler — sparrow.* only, coloured, human-readable
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(_ColorConsoleFormatter())
    ch.addFilter(lambda r: r.name.startswith("sparrow"))
    root.addHandler(ch)

    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _tail_log_lines(path: Path, n: int) -> list[str]:
    """Efficiently read last n lines from a file."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            buf = bytearray()
            pos = size
            while pos > 0 and buf.count(b"\n") <= n + 1:
                read_size = min(8192, pos)
                pos -= read_size
                f.seek(pos)
                buf = f.read(read_size) + buf
            lines = buf.decode("utf-8", errors="replace").split("\n")
            return [l for l in lines if l.strip()][-n:]
    except Exception:
        return []


from .storage import Storage
from .models import (
    Download, LibraryItem, SparrowConfig, TorrentClientConfig,
    MediaType, Quality, DownloadStatus, TorrentClientType, MediaRequest,
    RequestStatus, RequestStrategy
)
from .services import torrent_client as tc_svc
from .services import search_engine
from .services import metadata_service as meta_svc
from .services import file_organizer
from .services import request_service
from .services import release_parser
from .services.library_view import build_library_view
from .services.curator import Curator
from .agents.service import AgentService
from .agents.models import JobStatus
from .agents.runtime import PRICING_SOURCE, rates_for_model, spend_snapshot
from .agents import resolution
from . import __version__
from .configuration import (
    apply_config_update, effective_anthropic_key, effective_tmdb_key, public_config,
    track_saved_config,
)
from .doctor import build_report as build_doctor_report
from .runtime_settings import (
    configured_origins, env_bool, validate_bind, websocket_origin_allowed,
)


DATA_DIR = str(Path(os.getenv("SPARROW_DATA_DIR", "./data")).expanduser().resolve(strict=False))
storage = Storage(DATA_DIR)
track_saved_config(storage.get_config)
curator: Optional[Curator] = None
agent_service: Optional[AgentService] = None

# Active WebSocket connections
ws_clients: set[WebSocket] = set()


async def broadcast(event: dict) -> None:
    dead = set()
    for ws in ws_clients:
        try:
            user = accounts.from_session(ws.cookies.get(COOKIE))
            if not user:
                await ws.close(code=1008)
                dead.add(ws)
                continue
            await ws.send_json(event if user['role'] == 'admin' else {'type': 'refresh'})
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


# ─── Suggestion helpers ───────────────────────────────────────────────────────

async def _haiku_title_expand(query: str, api_key: str) -> list[str]:
    """Ask claude-haiku to suggest canonical + related titles for a partial query."""
    if not api_key:
        return []
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        msg = await asyncio.wait_for(
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                tools=[{
                    "name": "suggest_titles",
                    "description": "Return canonical and related movie/TV titles",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "titles": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "5-6 canonical titles",
                            }
                        },
                        "required": ["titles"],
                    },
                }],
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": f"""User typed: "{query}"

Suggest 5-6 movie/TV titles they might be searching for:
1. The exact canonical title (fix spelling, expand abbreviations — 'dbz'→'Dragon Ball Z')
2. 3-4 related titles (same franchise, genre, or vibe)
3. 1 broader similar title

Examples:
- "dbz" → ["Dragon Ball Z", "Dragon Ball Super", "Naruto", "One Piece", "Bleach"]
- "meth teacher" → ["Breaking Bad", "Better Call Saul", "Ozark", "Weeds"]
- "demon sl" → ["Demon Slayer: Kimetsu no Yaiba", "Jujutsu Kaisen", "Attack on Titan", "Bleach"]

Canonical titles only. No explanations."""}],
            ),
            timeout=8.0,
        )
        for block in msg.content:
            if block.type == "tool_use" and block.name == "suggest_titles":
                return block.input.get("titles", [])
    except Exception:
        pass
    return []


# ─── Media-type classification ────────────────────────────────────────────────

def _classify_media_type(torrent_name: str) -> MediaType:
    """Classify a torrent name as movie, tv, or unknown via the release parser."""
    try:
        parsed = release_parser.parse_release_name(torrent_name)
        if parsed.media_type == "tv":
            return MediaType.TV
        if parsed.media_type == "movie":
            return MediaType.MOVIE
    except Exception:
        pass
    return MediaType.UNKNOWN


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global curator, agent_service
    _setup_logging(DATA_DIR)
    await storage.load_all()
    curator = Curator(storage, DATA_DIR, broadcast)
    agent_service = AgentService(storage, DATA_DIR, broadcast)
    subtitles.attach(agent_service)
    subtitles.recover()
    discovery.register()
    from .services import managed_transmission
    # Bring back Sparrow's own Transmission without delaying startup.
    downloader_task = asyncio.create_task(managed_transmission.start_from_config(storage))
    task1 = asyncio.create_task(download_progress_loop())
    task2 = asyncio.create_task(artwork_enrichment_loop())
    task3 = asyncio.create_task(seeding_enforcer_loop())
    await agent_service.start()
    from .agents.setup_info import log_setup
    log_setup(DATA_DIR)
    migration_task=asyncio.create_task(catalogue.migrate_verified_legacy(broadcast))
    # v3: the deterministic curator is demoted — its loop only runs if
    # explicitly re-enabled. Agents own decisions now.
    task4 = None
    if os.getenv("SPARROW_LEGACY_CURATOR") == "1":
        task4 = asyncio.create_task(curator.run_forever())
    yield
    await subtitles.stop()
    migration_task.cancel()
    curator.stop()
    await agent_service.shutdown()
    await nodes.local().shutdown()
    downloader_task.cancel()
    await managed_transmission.shutdown()
    task1.cancel()
    task2.cancel()
    task3.cancel()
    if task4:
        task4.cancel()


app = FastAPI(title="Sparrow", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not websocket_origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host", "")):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user = accounts.from_session(websocket.cookies.get(COOKIE))
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    ws_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_clients.discard(websocket)


# ─── Download progress polling ────────────────────────────────────────────────

def _legacy_active_downloads(downloads: list[Download]) -> list[Download]:
    """Transfers owned by legacy v2 only; v3 AgentService owns its own landing."""
    return [
        download for download in downloads
        if download.status in (
            DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.SEEDING)
        and download.torrent_hash
        and not download.metadata.get("agent_managed")
    ]

async def download_progress_loop():
    """Background task: poll torrent client every 3s and update download records."""
    log = logging.getLogger("sparrow.downloads")
    while True:
        try:
            await asyncio.sleep(3)
            config = storage.get_config()
            if config.torrent_client.type == TorrentClientType.NONE:
                continue

            manager = tc_svc.TorrentManager(config.torrent_client)
            # v3 agent-managed transfers are owned end-to-end by AgentService.
            # The legacy poller must not consume their landing transition.
            active_downloads = _legacy_active_downloads(storage.get_all_downloads())

            for dl in active_downloads:
                status_data = await manager.get_torrent_status(dl.torrent_hash)
                if not status_data:
                    continue

                new_status = status_data["status"]
                progress = status_data["progress"]

                updates = {
                    "status": new_status,
                    "progress": progress,
                    "size_bytes": status_data["size_bytes"],
                    "downloaded_bytes": status_data["downloaded_bytes"],
                    "download_speed": status_data["download_speed"],
                    "eta_seconds": status_data["eta_seconds"],
                }

                if new_status != dl.status:
                    log.info("Status changed", extra={
                        "dl_name": dl.name, "from": dl.status.value, "to": new_status.value,
                        "hash": dl.torrent_hash, "progress": round(progress * 100, 1),
                    })

                if new_status == DownloadStatus.SEEDING and dl.status == DownloadStatus.DOWNLOADING:
                    updates["completed_at"] = time.time()
                    updates["staging_path"] = status_data.get("save_path", "")

                    # v3: agent-managed downloads belong to the Media Agent —
                    # enrich_download would clobber the job metadata, and
                    # auto-organize would race the agent.
                    if not dl.metadata.get("agent_managed"):
                        asyncio.create_task(enrich_download(dl.id))
                        if config.auto_organize:
                            asyncio.create_task(auto_organize_download(dl.id))

                await storage.update_download(dl.id, **updates)
                await update_requests_for_download(dl.id)
                await broadcast({"type": "download_update", "data": {**dl.to_dict(), **updates, "status": new_status.value}})

        except asyncio.CancelledError:
            break
        except Exception:
            pass


def _find_existing_library_item(tmdb_id, title: str, media_type: MediaType):
    for existing in storage.get_library():
        if existing.media_type != media_type:
            continue
        if tmdb_id and existing.tmdb_id == tmdb_id:
            return existing
        if not tmdb_id and title and existing.title.lower() == title.lower():
            return existing
    return None


def _series_root(dst: Path) -> Path:
    """Given a placed file path, walk up to the show's root folder."""
    for parent in dst.parents:
        if parent.name == "TV Shows":
            rel = dst.relative_to(parent)
            if rel.parts:
                return parent / rel.parts[0]
    return dst.parent


async def update_requests_for_download(download_id: str):
    """Refresh request progress when a linked download changes state."""
    dl = storage.get_download(download_id)
    if dl and dl.metadata.get("goal_id"):
        # Curator-managed download: the curator owns goal status, computed
        # from the episode inventory rather than download states.
        if curator:
            curator.poke()
        return
    for req in storage.get_requests():
        if download_id not in req.download_ids:
            continue
        linked = [storage.get_download(dl_id) for dl_id in req.download_ids]
        linked = [dl for dl in linked if dl]
        if not linked:
            continue
        complete_states = {DownloadStatus.ORGANIZED, DownloadStatus.COMPLETED, DownloadStatus.SEEDING}
        done = sum(1 for dl in linked if dl.status in complete_states)
        if done == len(linked):
            status = RequestStatus.COMPLETE if not req.missing_episodes else RequestStatus.PARTIAL
        elif any(dl.status == DownloadStatus.ERROR for dl in linked):
            status = RequestStatus.PARTIAL if done else RequestStatus.FAILED
        elif any(dl.status == DownloadStatus.ORGANIZING for dl in linked):
            status = RequestStatus.ORGANIZING
        else:
            status = RequestStatus.DOWNLOADING
        refreshed = await storage.update_request(req.id, status=status, progress_found=done)
        if refreshed:
            await broadcast({"type": "request_update", "data": refreshed.to_dict()})


async def enrich_download(download_id: str):
    """Fetch TMDB metadata for a completed download."""
    log = logging.getLogger("sparrow.downloads")
    dl = storage.get_download(download_id)
    if not dl:
        return
    config = storage.get_config()
    art_dir = Path(DATA_DIR) / "art"
    art_dir.mkdir(exist_ok=True)
    try:
        meta = await meta_svc.fetch_metadata_for_torrent(
            torrent_name=dl.name,
            media_type=dl.media_type,
            api_key=config.tmdb_api_key,
            art_cache_dir=art_dir,
        )
        await storage.update_download(download_id, metadata=meta, quality=meta.get("quality", ""))
        if meta.get("tmdb_id"):
            log.info("Metadata fetched", extra={
                "dl_name": dl.name, "tmdb_id": meta["tmdb_id"],
                "title": meta.get("title"), "media_type": dl.media_type.value,
            })
        else:
            log.warning("Metadata not found", extra={"dl_name": dl.name, "media_type": dl.media_type.value})
    except Exception as e:
        log.warning("Metadata fetch failed", extra={"dl_name": dl.name, "error": str(e)})


async def auto_organize_download(download_id: str):
    """Automatically organize a completed download into the library."""
    log = logging.getLogger("sparrow.organize")
    await asyncio.sleep(2)  # Give the torrent client a moment
    dl = storage.get_download(download_id)
    if not dl or not dl.staging_path:
        return
    config = storage.get_config()
    if not config.library_dir:
        return

    log.info("Organizing", extra={"dl_name": dl.name, "staging": dl.staging_path, "media_type": dl.media_type.value})
    await storage.update_download(download_id, status=DownloadStatus.ORGANIZING)
    await broadcast({"type": "download_update", "data": {"id": download_id, "status": "organizing"}})

    try:
        ops = await file_organizer.organize_download(
            staging_path=dl.staging_path,
            library_dir=config.library_dir,
            media_type=dl.media_type,
            metadata=dl.metadata,
            data_dir=DATA_DIR,
            anthropic_api_key=config.anthropic_api_key,
            storage=storage,
        )
        successful = [op for op in ops if op["success"]]
        if successful:
            verification = file_organizer.verify_organized_operations(ops, dl.media_type, dl.metadata)
            library_path = successful[0]["dst"]
            updated_metadata = {**(dl.metadata or {}), "_organize_verification": verification}
            await storage.update_download(
                download_id,
                status=DownloadStatus.ORGANIZED,
                library_path=library_path,
                metadata=updated_metadata,
            )
            if not verification["ok"]:
                log.warning("Organize verification found issues", extra={
                    "dl_name": dl.name,
                    "issues": "; ".join(verification["issues"][:3]),
                })

            # Resolve UNKNOWN media_type from where the file was placed
            resolved_type = dl.media_type
            if dl.media_type == MediaType.UNKNOWN:
                if "/Movies/" in library_path or "\\Movies\\" in library_path:
                    resolved_type = MediaType.MOVIE
                elif "/TV Shows/" in library_path or "\\TV Shows\\" in library_path:
                    resolved_type = MediaType.TV
                else:
                    resolved_type = MediaType.MOVIE

            # Merge into the library: one item per title, per-episode inventory
            meta = updated_metadata
            item = _find_existing_library_item(meta.get("tmdb_id"), meta.get("title", dl.name), resolved_type)
            if item is None:
                item = LibraryItem(
                    id=str(uuid.uuid4()),
                    title=meta.get("title", dl.name),
                    media_type=resolved_type,
                    path=str(Path(library_path).parent),
                    year=meta.get("year"),
                    tmdb_id=meta.get("tmdb_id"),
                    imdb_id=meta.get("imdb_id"),
                    overview=meta.get("overview", ""),
                    poster_path=meta.get("poster_path", ""),
                    backdrop_path=meta.get("backdrop_path", ""),
                    genres=meta.get("genres", []),
                    rating=meta.get("rating"),
                    seasons=meta.get("seasons"),
                    episode_count=meta.get("episode_count"),
                    size_bytes=dl.size_bytes,
                    metadata=meta,
                )
                if resolved_type == MediaType.TV:
                    item.path = str(_series_root(Path(library_path)))
                await storage.add_library_item(item)
            else:
                item.size_bytes += dl.size_bytes

            # Record every placed episode in the inventory; replace upgraded files
            for op in successful:
                for s, e in op.get("episodes") or []:
                    old = item.episode_file(s, e)
                    if old and old.get("path") and old["path"] != op["dst"]:
                        try:
                            old_path = Path(old["path"])
                            if old_path.exists():
                                old_path.unlink()
                                item.size_bytes = max(0, item.size_bytes - int(old.get("size_bytes") or 0))
                                log.info("Replaced episode file with better version", extra={
                                    "old": old["path"], "new": op["dst"],
                                })
                        except Exception:
                            pass
                    try:
                        size = Path(op["dst"]).stat().st_size
                    except OSError:
                        size = 0
                    item.set_episode_file(s, e, {
                        "quality": op.get("quality", "unknown"),
                        "path": op["dst"],
                        "size_bytes": size,
                        "added_at": time.time(),
                        "verified": True,
                    })
            await storage.update_library_item(
                item.id,
                episodes=item.episodes,
                size_bytes=item.size_bytes,
                metadata={**item.metadata, "_organize_verification": verification},
            )
            await broadcast({"type": "library_update", "data": item.to_dict()})
            log.info("Organized", extra={
                "dl_name": dl.name, "dst": library_path,
                "title": item.title, "media_type": resolved_type.value, "tmdb_id": item.tmdb_id,
            })
            if curator:
                curator.poke()

            # Remove torrent from client after successful organize (files are already moved)
            if dl.torrent_hash and config.torrent_client.type != TorrentClientType.NONE:
                try:
                    organize_manager = tc_svc.TorrentManager(config.torrent_client)
                    await organize_manager.delete_torrent(dl.torrent_hash, delete_files=False)
                    log.info("Torrent removed after organize", extra={"dl_name": dl.name, "hash": dl.torrent_hash})
                except Exception as e:
                    log.warning("Could not remove torrent after organize", extra={"dl_name": dl.name, "error": str(e)})
        else:
            errors = "; ".join(op["error"] for op in ops if op["error"])
            log.error("Organize failed", extra={"dl_name": dl.name, "errors": errors})
            await storage.update_download(download_id, status=DownloadStatus.ERROR, error_message=errors)
    except Exception as e:
        log.error("Organize exception", extra={"dl_name": dl.name, "error": str(e)})
        await storage.update_download(download_id, status=DownloadStatus.ERROR, error_message=str(e))

    updated = storage.get_download(download_id)
    if updated:
        await broadcast({"type": "download_update", "data": updated.to_dict()})


async def artwork_enrichment_loop():
    """
    Periodically re-fetch metadata for library items and downloads that
    have no poster art. Runs every 5 minutes.
    """
    log = logging.getLogger("sparrow.artwork")
    await asyncio.sleep(30)  # initial delay, let server settle
    while True:
        try:
            config = storage.get_config()
            if config.tmdb_api_key:
                art_dir = Path(DATA_DIR) / "art"
                art_dir.mkdir(exist_ok=True)

                # Fix library items with missing poster / stale metadata
                for item in storage.get_library():
                    needs_artwork = True
                    if item.poster_path:
                        art_file = Path(DATA_DIR) / item.poster_path.lstrip("/")
                        if art_file.exists():
                            needs_artwork = False

                    # Backfill size_bytes from filesystem when missing
                    if item.size_bytes == 0 and item.path:
                        try:
                            p = Path(item.path)
                            if p.exists():
                                size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                                if size > 0:
                                    await storage.update_library_item(item.id, size_bytes=size)
                        except Exception:
                            pass

                    if not needs_artwork:
                        continue

                    retries = (item.metadata or {}).get("_enrich_attempts", 0)
                    if retries >= 3:
                        continue
                    try:
                        meta = await meta_svc.fetch_metadata_for_torrent(
                            torrent_name=item.title,
                            media_type=item.media_type,
                            api_key=config.tmdb_api_key,
                            art_cache_dir=art_dir,
                            tmdb_id=item.tmdb_id,
                        )
                        merged_metadata = {**item.metadata, **meta, "_enrich_attempts": 0}
                        if item.metadata.get("verified"):
                            # Agent inventory values came from ffprobe. Artwork/title
                            # enrichment may fill blanks but never replace those facts
                            # with a release-name guess such as quality="unknown".
                            for key in ("verified", "duration_minutes", "quality",
                                        "source_download_id", "added_at"):
                                if key in item.metadata:
                                    merged_metadata[key] = item.metadata[key]
                        updates: dict = {"metadata": merged_metadata}
                        if meta.get("poster_path"):
                            updates["poster_path"] = meta["poster_path"]
                        if meta.get("backdrop_path"):
                            updates["backdrop_path"] = meta["backdrop_path"]
                        if meta.get("tmdb_id") and not item.tmdb_id:
                            updates["tmdb_id"] = meta["tmdb_id"]
                        if meta.get("overview") and not item.overview:
                            updates["overview"] = meta["overview"]
                        if meta.get("rating") and not item.rating:
                            updates["rating"] = meta["rating"]
                        if meta.get("genres") and not item.genres:
                            updates["genres"] = meta["genres"]
                        # Resolve unknown media_type
                        if item.media_type == MediaType.UNKNOWN and meta.get("tmdb_id"):
                            # Determine from path if file was placed in Movies/ or TV Shows/
                            if "/Movies/" in item.path or "\\Movies\\" in item.path:
                                updates["media_type"] = MediaType.MOVIE
                            elif "/TV Shows/" in item.path or "\\TV Shows\\" in item.path:
                                updates["media_type"] = MediaType.TV
                        if meta.get("poster_path"):
                            log.info("Artwork enriched", extra={"item": item.title, "poster": meta["poster_path"]})
                        else:
                            updates["metadata"] = {**item.metadata, "_enrich_attempts": retries + 1}
                        refreshed = await storage.update_library_item(item.id, **updates)
                        if refreshed:
                            await broadcast({"type": "library_update", "data": refreshed.to_dict()})
                    except Exception:
                        pass

                # Fix downloads with no poster in metadata
                for dl in storage.get_all_downloads():
                    if dl.status not in (DownloadStatus.SEEDING, DownloadStatus.COMPLETED,
                                         DownloadStatus.ORGANIZED, DownloadStatus.ORGANIZING):
                        continue
                    if (dl.metadata or {}).get("poster_path"):
                        continue
                    retries = (dl.metadata or {}).get("_enrich_attempts", 0)
                    if retries >= 3:
                        continue
                    try:
                        meta = await meta_svc.fetch_metadata_for_torrent(
                            torrent_name=dl.name,
                            media_type=dl.media_type,
                            api_key=config.tmdb_api_key,
                            art_cache_dir=art_dir,
                        )
                        updated_meta = {**(dl.metadata or {}), **meta, "_enrich_attempts": 0}
                        await storage.update_download(dl.id, metadata=updated_meta)
                        refreshed = storage.get_download(dl.id)
                        await broadcast({"type": "download_update", "data": (refreshed or dl).to_dict()})
                    except Exception:
                        updated_meta = {**(dl.metadata or {}), "_enrich_attempts": retries + 1}
                        await storage.update_download(dl.id, metadata=updated_meta)

        except Exception:
            pass

        await asyncio.sleep(300)  # 5 minutes


async def seeding_enforcer_loop():
    """
    Stop seeding torrents that have exceeded the configured ratio or time limit.
    Also sweeps stale Transmission entries for already-organized downloads when seeding is disabled.
    """
    log = logging.getLogger("sparrow.seeding")
    await asyncio.sleep(10)  # short initial delay for startup sweep

    # Zero means unlimited, including after a server restart.

    while True:
        try:
            config = storage.get_config()
            ratio_limit = config.seeding_ratio_limit
            time_limit_s = config.seeding_time_hours * 3600

            if ratio_limit <= 0 and time_limit_s <= 0:
                await asyncio.sleep(60)
                continue

            if config.torrent_client.type == TorrentClientType.NONE:
                await asyncio.sleep(60)
                continue

            manager = tc_svc.TorrentManager(config.torrent_client)
            seeding_downloads = [
                dl for dl in storage.get_all_downloads()
                if dl.status == DownloadStatus.SEEDING and dl.torrent_hash and not dl.metadata.get('node_id')
            ]

            for dl in seeding_downloads:
                try:
                    status = await manager.get_torrent_status(dl.torrent_hash)
                    if not status:
                        continue

                    ratio = status.get("upload_ratio", 0.0)
                    seeding_secs = status.get("seeding_time", 0)

                    should_stop = False
                    if ratio_limit > 0 and ratio >= ratio_limit:
                        should_stop = True
                        log.info("Stopping seeding: ratio limit reached",
                                 extra={"dl_name": dl.name, "ratio": ratio, "limit": ratio_limit})
                    elif time_limit_s > 0 and seeding_secs >= time_limit_s:
                        should_stop = True
                        log.info("Stopping seeding: time limit reached",
                                 extra={"dl_name": dl.name, "hours": seeding_secs / 3600})

                    if should_stop:
                        # Remove from Transmission (no file deletion) — keeps Sparrow history
                        await manager.delete_torrent(dl.torrent_hash, delete_files=False)
                        await storage.update_download(dl.id, status=DownloadStatus.COMPLETED)
                        refreshed = storage.get_download(dl.id)
                        await broadcast({"type": "download_update", "data": (refreshed or dl).to_dict()})
                except Exception:
                    pass

        except Exception:
            pass

        await asyncio.sleep(60)


# ─── Pydantic request models ──────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    staging_dir: Optional[str] = None
    library_dir: Optional[str] = None
    torrent_client: Optional[dict] = None
    quality_preference: Optional[str] = None
    tmdb_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    onboarding_complete: Optional[bool] = None
    auto_organize: Optional[bool] = None
    seeding_ratio_limit: Optional[float] = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    seeding_time_hours: Optional[float] = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    prefer_smaller_files: Optional[bool] = None
    prefer_season_packs: Optional[bool] = None
    season_pack_size_limit_gb: Optional[float] = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    max_active_transfers: Optional[int] = Field(default=None, ge=1, le=50)
    preferred_search_engines: Optional[list[str]] = None
    smart_model: Optional[str] = None
    cheap_model: Optional[str] = None
    clear_tmdb_api_key: bool = False
    clear_anthropic_api_key: bool = False
    clear_openai_api_key: bool = False


class AddDownloadRequest(BaseModel):
    magnet_url: str
    name: str = ""
    media_type: str = "unknown"
    tmdb_id: Optional[int] = None


class OrganizeRequest(BaseModel):
    download_id: str
    media_type: Optional[str] = None
    tmdb_id: Optional[int] = None


class CreateMediaRequest(BaseModel):
    query: str
    quality: Optional[str] = None


class CreateSelectedMediaRequest(BaseModel):
    query: str
    quality: Optional[str] = None
    resolved: dict = {}
    selected: dict = {}
    selected_items: list[dict] = []
    strategy: str = "movie"
    score: dict = {}
    summary: str = ""


# ─── Config routes ────────────────────────────────────────────────────────────

@app.get("/api/version")
async def version():
    return {"version": __version__, "release": "alpha"}

@app.get("/api/config")
async def get_config():
    return public_config(storage.get_config())


@app.patch("/api/config")
async def update_config(update: ConfigUpdate):
    config = SparrowConfig.from_dict(storage.get_config().to_dict())
    try:
        config = apply_config_update(config, update.model_dump())
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    await storage.save_config(config)
    return public_config(config)


@app.get("/api/config/onboarding-status")
async def onboarding_status():
    config = storage.get_config()
    return {
        "complete": config.onboarding_complete,
        "has_staging": bool(config.staging_dir),
        "has_library": bool(config.library_dir),
        "has_torrent_client": config.torrent_client.type != TorrentClientType.NONE,
        "has_tmdb_key": bool(effective_tmdb_key(config)),
        "has_anthropic_key": bool(effective_anthropic_key(config)),
    }


@app.get("/api/doctor")
async def doctor_report():
    """Dependency/configuration readiness without exposing any credentials."""
    return await build_doctor_report(storage)


# ─── Torrent client routes ────────────────────────────────────────────────────

@app.get("/api/torrent-clients/discover")
async def discover_clients():
    clients = await tc_svc.discover_torrent_clients()
    return [c.to_dict() for c in clients]


@app.post("/api/torrent-clients/test")
async def test_client(body: dict):
    cfg = TorrentClientConfig.from_dict(body)
    manager = tc_svc.TorrentManager(cfg)
    info = await manager.get_info()
    return info.to_dict()


@app.get("/api/torrent-clients/status")
async def client_status():
    config = storage.get_config()
    if config.torrent_client.type == TorrentClientType.NONE:
        return {"configured": False}
    manager = tc_svc.TorrentManager(config.torrent_client)
    info = await manager.get_info()
    return info.to_dict()


@app.get("/api/health")
async def health_overview():
    """Consumer-facing health with actionable fixes."""
    config = storage.get_config()
    issues: list[dict] = []

    client_info = {"configured": False}
    if config.torrent_client.type == TorrentClientType.NONE:
        issues.append({
            "id": "torrent_client_missing",
            "title": "Torrent client is not configured",
            "detail": "Sparrow needs qBittorrent or Transmission before it can queue downloads.",
            "actions": ["Open Settings", "Auto-detect clients"],
        })
    else:
        manager = tc_svc.TorrentManager(config.torrent_client)
        info = await manager.get_info()
        client_info = info.to_dict()
        if not info.reachable:
            issues.append({
                "id": "torrent_client_unreachable",
                "title": "Torrent client is not reachable",
                "detail": f"{config.torrent_client.type.value} is configured on {config.torrent_client.host}:{config.torrent_client.port}, but Sparrow cannot connect.",
                "actions": ["Start the client", "Enable remote/web access", "Re-detect clients"],
            })

    if not config.staging_dir:
        issues.append({
            "id": "staging_missing",
            "title": "Staging folder is missing",
            "detail": "Downloads need a temporary location before they are organized.",
            "actions": ["Choose staging folder in Settings"],
        })
    elif not Path(config.staging_dir).exists():
        issues.append({
            "id": "staging_not_found",
            "title": "Staging folder does not exist",
            "detail": f"Sparrow is configured to use {config.staging_dir}, but that folder is not on disk.",
            "actions": ["Create folder", "Choose a different folder"],
        })
    if not config.library_dir:
        issues.append({
            "id": "library_missing",
            "title": "Library folder is missing",
            "detail": "Organized movies and TV shows need a destination folder.",
            "actions": ["Choose library folder in Settings"],
        })
    elif not Path(config.library_dir).exists():
        issues.append({
            "id": "library_not_found",
            "title": "Library folder does not exist",
            "detail": f"Sparrow is configured to use {config.library_dir}, but that folder is not on disk.",
            "actions": ["Create folder", "Choose a different folder"],
        })
    if not config.tmdb_api_key:
        if not effective_tmdb_key(config):
            issues.append({
                "id": "tmdb_missing",
                "title": "Metadata key is missing",
                "detail": "TMDB powers posters, canonical titles, seasons, and episode counts.",
                "actions": ["Add TMDB key"],
            })

    if not effective_anthropic_key(config):
        issues.append({
            "id": "anthropic_missing",
            "title": "Reasoning key is missing",
            "detail": "Fetch, Media, and Librarian agents need an Anthropic API key.",
            "actions": ["Add Anthropic key"],
        })

    if not shutil.which("ffprobe"):
        issues.append({
            "id": "ffprobe_missing",
            "title": "Media verification is unavailable",
            "detail": "Install ffmpeg/ffprobe so Sparrow can verify what actually downloaded.",
            "actions": ["Install ffmpeg", "Run python -m backend.doctor"],
        })

    if config.staging_dir and config.library_dir:
        staging = Path(config.staging_dir).expanduser().resolve(strict=False)
        library = Path(config.library_dir).expanduser().resolve(strict=False)
        if staging == library or staging in library.parents or library in staging.parents:
            issues.append({
                "id": "media_roots_overlap",
                "title": "Staging and library folders overlap",
                "detail": "Incomplete downloads can be mistaken for library media. Use sibling folders.",
                "actions": ["Use ~/Sparrow/Temp and ~/Sparrow/Library"],
            })

    return {
        "healthy": not issues,
        "issues": issues,
        "summary": {
            "requests": len(storage.get_requests()),
            "downloads": len(storage.get_all_downloads()),
            "library": len(storage.get_library()),
            "torrent_client": client_info,
        },
    }


@app.post("/api/health/actions/{issue_id}")
async def health_action(issue_id: str):
    """Try the simplest safe fix for a health issue, then return fresh health."""
    config = storage.get_config()
    applied = False
    message = ""

    if issue_id in ("torrent_client_missing", "torrent_client_unreachable"):
        recovered_current = False
        recovery_message = ""
        if (issue_id == "torrent_client_unreachable" and
                config.torrent_client.type != TorrentClientType.NONE):
            started, recovery_message = await tc_svc.start_configured_client(
                config.torrent_client)
            if started:
                manager = tc_svc.TorrentManager(config.torrent_client)
                for _ in range(8):
                    await asyncio.sleep(1)
                    if (await manager.get_info()).reachable:
                        recovered_current = True
                        break
        clients = [] if recovered_current else await tc_svc.discover_torrent_clients()
        if recovered_current:
            applied = True
            message = f"{recovery_message} Connected successfully."
        elif clients:
            chosen = clients[0]
            config.torrent_client = TorrentClientConfig(
                type=chosen.type,
                host=chosen.host,
                port=chosen.port,
            )
            await storage.save_config(config)
            applied = True
            message = f"Connected to {chosen.type.value} on {chosen.host}:{chosen.port}."
        else:
            message = recovery_message or (
                "No reachable download app was found. Install Transmission or qBittorrent, "
                "enable remote/web access, then run this again."
            )
    elif issue_id == "staging_not_found" and config.staging_dir:
        Path(config.staging_dir).mkdir(parents=True, exist_ok=True)
        applied = True
        message = f"Created staging folder: {config.staging_dir}"
    elif issue_id == "library_not_found" and config.library_dir:
        Path(config.library_dir).mkdir(parents=True, exist_ok=True)
        applied = True
        message = f"Created library folder: {config.library_dir}"
    else:
        message = "This issue needs a setting change from the user."

    overview = await health_overview()
    return {"applied": applied, "message": message, "health": overview}


# ─── Suggestion routes ────────────────────────────────────────────────────────

@app.get("/api/search/suggest")
async def search_suggest(q: str = Query(..., min_length=2)):
    """Fast suggestions: local library/downloads + direct TMDB multi-search."""
    _slog = logging.getLogger("sparrow.suggest")
    config = storage.get_config()
    from .services.search_engine import _check_library, _check_downloads
    library = _check_library(q, DATA_DIR)
    downloads = _check_downloads(q, DATA_DIR)
    tmdb: list[dict] = []
    if config.tmdb_api_key:
        tmdb = await meta_svc.tmdb_quick_suggest(q, config.tmdb_api_key)
    _slog.info("Suggest", extra={"q": q, "library": len(library), "downloads": len(downloads), "tmdb": len(tmdb)})
    return {"library": library, "downloads": downloads, "tmdb": tmdb}


@app.get("/api/search/suggest/expand")
async def search_suggest_expand(q: str = Query(..., min_length=2)):
    """Haiku-expanded suggestions: canonical + related titles, each TMDB-enriched."""
    _slog = logging.getLogger("sparrow.suggest")
    config = storage.get_config()
    if not config.anthropic_api_key or not config.tmdb_api_key:
        return {"suggestions": []}
    _slog.info("Expand (haiku)", extra={"q": q})
    titles = await _haiku_title_expand(q, config.anthropic_api_key)
    if not titles:
        return {"suggestions": []}
    _slog.info("Haiku titles", extra={"q": q, "titles": titles})
    tasks = [meta_svc.tmdb_quick_suggest(t, config.tmdb_api_key) for t in titles[:6]]
    per_title = await asyncio.gather(*tasks, return_exceptions=True)
    seen: set[int] = set()
    suggestions = []
    for results in per_title:
        if isinstance(results, Exception) or not results:
            continue
        r = results[0]
        if r["tmdb_id"] not in seen:
            seen.add(r["tmdb_id"])
            suggestions.append(r)
    _slog.info("Expand done", extra={"q": q, "suggestions": len(suggestions)})
    return {"suggestions": suggestions}


@app.get("/api/search/suggest/tv/{tmdb_id}")
async def search_suggest_tv(tmdb_id: int):
    """Season list for a TV show — powers the episode picker in the dropdown."""
    config = storage.get_config()
    if not config.tmdb_api_key:
        return {"seasons": []}
    seasons = await meta_svc.tmdb_tv_seasons(tmdb_id, config.tmdb_api_key)
    return {"seasons": seasons}


# ─── Search routes ────────────────────────────────────────────────────────────

@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1),
    type: Optional[str] = Query(None),
    quality: Optional[str] = Query(None),
    limit: int = Query(30, le=100),
):
    config = storage.get_config()
    media_type = MediaType(type) if type else None
    q_pref = Quality(quality) if quality else config.quality_preference

    results = await search_engine.search(
        q,
        media_type=media_type,
        quality=q_pref,
        limit=limit,
        data_dir=DATA_DIR,
        tmdb_api_key=config.tmdb_api_key,
        anthropic_api_key=config.anthropic_api_key,
    )
    return [r.to_dict() for r in results]


@app.get("/api/search/stream")
async def search_stream(
    q: str = Query(..., min_length=1),
    type: Optional[str] = Query(None),
    quality: Optional[str] = Query(None),
    limit: int = Query(30, le=100),
):
    """Server-Sent Events endpoint — streams search progress events in real time."""
    config = storage.get_config()
    media_type = MediaType(type) if type else None
    q_pref = Quality(quality) if quality else config.quality_preference

    async def event_generator():
        try:
            async for event in search_engine.search_stream(
                q,
                media_type=media_type,
                quality=q_pref,
                limit=limit,
                data_dir=DATA_DIR,
                tmdb_api_key=config.tmdb_api_key,
                anthropic_api_key=config.anthropic_api_key,
                prefer_smaller=config.prefer_smaller_files,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/search/deeper/stream")
async def search_deeper_stream_endpoint(
    q: str = Query(..., min_length=1),
    exclude: str = Query(""),  # comma-separated existing info_hashes
    type: Optional[str] = Query(None),
    quality: Optional[str] = Query(None),
    limit: int = Query(20, le=50),
):
    """SSE — find alternatives when existing results aren't satisfactory."""
    config = storage.get_config()
    media_type = MediaType(type) if type else None
    q_pref = Quality(quality) if quality else config.quality_preference
    existing_hashes = [h.strip() for h in exclude.split(",") if h.strip()]

    async def event_generator():
        try:
            async for event in search_engine.search_deeper_stream(
                q,
                existing_hashes=existing_hashes,
                media_type=media_type,
                quality=q_pref,
                limit=limit,
                data_dir=DATA_DIR,
                anthropic_api_key=config.anthropic_api_key,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/search/season/stream")
async def season_search_stream(
    title: str = Query(...),
    season: int = Query(...),
    episodes: int = Query(...),
    tmdb_id: Optional[int] = Query(None),
    quality: Optional[str] = Query(None),
):
    """SSE — search for every episode in a TV season and queue each as a download."""
    config = storage.get_config()
    if config.torrent_client.type == TorrentClientType.NONE:
        raise HTTPException(400, "No torrent client configured.")
    if not config.staging_dir:
        raise HTTPException(400, "No staging directory configured.")

    q_pref = Quality(quality) if quality else config.quality_preference
    dl_log = logging.getLogger("sparrow.downloads")

    async def event_generator():
        found = 0
        missing: list[int] = []

        for ep_num in range(1, episodes + 1):
            yield f"data: {json.dumps({'type': 'episode_start', 'episode': ep_num, 'total': episodes})}\n\n"

            try:
                result = await search_engine.search_episode_torrent(
                    title=title, season=season, episode=ep_num,
                    quality=q_pref, data_dir=DATA_DIR, cat="208",
                )
            except Exception as e:
                dl_log.error("Season episode search error", extra={"show": title, "season": season, "episode": ep_num, "error": str(e)})
                missing.append(ep_num)
                yield f"data: {json.dumps({'type': 'episode_missing', 'episode': ep_num})}\n\n"
                await asyncio.sleep(0)
                continue

            if result:
                try:
                    m = re.search(r"xt=urn:btih:([0-9a-fA-F]+)", result.magnet_url, re.IGNORECASE)
                    info_hash = m.group(1).lower() if m else str(uuid.uuid4())

                    existing = storage.find_download_by_hash(info_hash)
                    if existing:
                        found += 1
                        yield f"data: {json.dumps({'type': 'episode_found', 'episode': ep_num, 'torrent_name': result.name, 'download_id': existing.id, 'duplicate': True})}\n\n"
                        await asyncio.sleep(0)
                        continue

                    manager = tc_svc.TorrentManager(config.torrent_client)
                    torrent_hash = await manager.add_magnet(result.magnet_url, config.staging_dir)

                    dl_id = str(uuid.uuid4())
                    dl = Download(
                        id=dl_id,
                        name=result.name,
                        magnet_url=result.magnet_url,
                        media_type=MediaType.TV,
                        status=DownloadStatus.QUEUED,
                        torrent_hash=torrent_hash or info_hash,
                        tmdb_id=tmdb_id,
                    )
                    await storage.add_download(dl)
                    await broadcast({"type": "download_added", "data": dl.to_dict()})
                    asyncio.create_task(enrich_download(dl_id))

                    dl_log.info("Season episode queued", extra={
                        "show": title, "season": season, "episode": ep_num,
                        "torrent_name": result.name,
                    })
                    found += 1
                    yield f"data: {json.dumps({'type': 'episode_found', 'episode': ep_num, 'torrent_name': result.name, 'download_id': dl_id})}\n\n"
                except Exception as e:
                    dl_log.error("Season episode queue error", extra={"show": title, "season": season, "episode": ep_num, "error": str(e)})
                    missing.append(ep_num)
                    yield f"data: {json.dumps({'type': 'episode_missing', 'episode': ep_num})}\n\n"
            else:
                missing.append(ep_num)
                dl_log.warning("Season episode not found", extra={"show": title, "season": season, "episode": ep_num})
                yield f"data: {json.dumps({'type': 'episode_missing', 'episode': ep_num})}\n\n"

            await asyncio.sleep(0)

        yield f"data: {json.dumps({'type': 'season_done', 'found': found, 'total': episodes, 'missing': missing})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/search/log")
async def search_log(limit: int = 50):
    """Recent search history with strategies tried and outcomes."""
    import json
    log_path = Path(DATA_DIR) / "search_log.json"
    if not log_path.exists():
        return []
    try:
        entries = json.loads(log_path.read_text())
        return list(reversed(entries[-limit:]))
    except Exception:
        return []


# ─── Request routes ───────────────────────────────────────────────────────────

@app.get("/api/requests")
async def list_requests():
    return [r.to_dict() for r in storage.get_requests()]


@app.post("/api/requests")
async def create_request(req: CreateMediaRequest):
    query = req.query.strip()
    if not query:
        raise HTTPException(400, "Request query is required")
    config = storage.get_config()
    quality = req.quality or config.quality_preference.value
    media_request = MediaRequest(
        id=str(uuid.uuid4()),
        query=query,
        quality=quality,
    )
    await storage.add_request(media_request)
    await broadcast({"type": "request_added", "data": media_request.to_dict()})
    asyncio.create_task(request_service.process_request(storage, media_request.id, DATA_DIR, broadcast))
    return media_request.to_dict()


@app.post("/api/requests/selected")
async def create_selected_request(req: CreateSelectedMediaRequest):
    query = req.query.strip()
    if not query:
        raise HTTPException(400, "Request query is required")
    selected_items = req.selected_items or ([req.selected] if req.selected else [])
    selected_items = [item for item in selected_items if item and item.get("magnet_url")]
    if not selected_items:
        raise HTTPException(400, "Selected release is missing a magnet URL")
    selected = selected_items[0]

    config = storage.get_config()
    if config.torrent_client.type == TorrentClientType.NONE:
        raise HTTPException(400, "No torrent client configured.")
    if not config.staging_dir:
        raise HTTPException(400, "No staging directory configured.")

    resolved = req.resolved or {}
    media_type = MediaType(resolved.get("media_type") or selected.get("media_type") or "unknown")
    tmdb_id = resolved.get("tmdb_id")
    try:
        season = int(resolved.get("season")) if resolved.get("season") is not None else None
    except Exception:
        season = None
    try:
        episode_count = int(resolved.get("episode_count")) if resolved.get("episode_count") is not None else None
    except Exception:
        episode_count = None

    strategy_value = req.strategy if req.strategy in RequestStrategy._value2member_map_ else ("season_pack" if media_type == MediaType.TV else "movie")
    media_request = MediaRequest(
        id=str(uuid.uuid4()),
        query=query,
        quality=req.quality or resolved.get("quality") or config.quality_preference.value,
        title=resolved.get("title") or query,
        media_type=media_type,
        season=season,
        episode_count=episode_count,
        tmdb_id=tmdb_id,
        status=RequestStatus.EVALUATING,
        strategy=RequestStrategy(strategy_value),
        decision_summary=req.summary or f"User selected release: {selected.get('name', 'torrent')}",
        evaluation={
            "selected": selected,
            "score": req.score,
            "strategy": strategy_value,
            "selection_method": "user_confirmed",
            "reason": req.summary,
        },
        progress_total=len(selected_items) or episode_count or 1,
    )
    await storage.add_request(media_request)
    await broadcast({"type": "request_added", "data": media_request.to_dict()})

    try:
        manager = tc_svc.TorrentManager(config.torrent_client)
        queued: list[Download] = []
        for item in selected_items:
            torrent_hash = await manager.add_magnet(item["magnet_url"], config.staging_dir)
            dl = Download(
                id=str(uuid.uuid4()),
                name=item.get("name") or "Selected release",
                magnet_url=item["magnet_url"],
                media_type=media_type,
                status=DownloadStatus.QUEUED,
                torrent_hash=torrent_hash or item.get("info_hash", ""),
                tmdb_id=tmdb_id,
                size_bytes=int(item.get("size_bytes") or 0),
            )
            await storage.add_download(dl)
            await storage.link_request_download(media_request.id, dl.id)
            queued.append(dl)
        await storage.update_request(
            media_request.id,
            status=RequestStatus.DOWNLOADING,
            progress_found=len(queued),
            download_ids=[dl.id for dl in queued],
        )
        for dl in queued:
            await broadcast({"type": "download_added", "data": dl.to_dict()})
        refreshed = storage.get_request(media_request.id)
        if refreshed:
            await broadcast({"type": "request_update", "data": refreshed.to_dict()})
            return refreshed.to_dict()
        return media_request.to_dict()
    except Exception as exc:
        await storage.update_request(media_request.id, status=RequestStatus.FAILED, error_message=str(exc))
        refreshed = storage.get_request(media_request.id)
        if refreshed:
            await broadcast({"type": "request_update", "data": refreshed.to_dict()})
            return refreshed.to_dict()
        raise


@app.post("/api/requests/preview")
async def preview_request(req: CreateMediaRequest):
    query = req.query.strip()
    if not query:
        raise HTTPException(400, "Request query is required")
    config = storage.get_config()
    quality = req.quality or config.quality_preference.value
    return await request_service.preview_request_plan(query, quality, config, DATA_DIR)


@app.get("/api/requests/{request_id}")
async def get_request(request_id: str):
    req = storage.get_request(request_id)
    if not req:
        raise HTTPException(404, "Request not found")
    return req.to_dict()


@app.post("/api/requests/{request_id}/retry")
async def retry_request(request_id: str):
    req = storage.get_request(request_id)
    if not req:
        raise HTTPException(404, "Request not found")
    refreshed = await storage.update_request(
        request_id,
        status=RequestStatus.PENDING,
        error_message="",
        decision_summary="Retry queued",
    )
    await broadcast({"type": "request_update", "data": refreshed.to_dict()})
    asyncio.create_task(request_service.process_request(storage, request_id, DATA_DIR, broadcast))
    return refreshed.to_dict()


@app.delete("/api/requests/{request_id}")
async def delete_request(request_id: str):
    ok = await storage.delete_request(request_id)
    if not ok:
        raise HTTPException(404, "Request not found")
    await broadcast({"type": "request_removed", "data": {"id": request_id}})
    return {"success": True}


# ─── v2: Shows / Goals / Activity ────────────────────────────────────────────

class CreateGoalRequest(BaseModel):
    tmdb_id: Optional[int] = None
    media_type: str = "unknown"
    title: str = ""
    query: str = ""
    seasons: Optional[list[int]] = None
    episodes: Optional[dict] = None      # {"2": [7, 9]}
    quality: str = ""
    min_quality: str = "any"


@app.get("/api/shows/{tmdb_id}")
async def get_show(tmdb_id: int):
    """A TV show with per-season/per-episode coverage: on disk, on the way, missing."""
    config = storage.get_config()
    details = await meta_svc.get_tv_details(tmdb_id, config.tmdb_api_key)
    if not details:
        raise HTTPException(404, "Show not found on TMDB")
    seasons_meta = await meta_svc.tmdb_tv_seasons(tmdb_id, config.tmdb_api_key)

    item = next((i for i in storage.get_library() if i.tmdb_id == tmdb_id and i.media_type == MediaType.TV), None)
    inventory = item.episodes if item else {}
    mandate = agent_service.store.get_mandate(tmdb_id, "tv")

    # Episodes on the way, from this show's goals' active downloads
    in_flight: set[tuple[int, int]] = set()
    goals = [r for r in storage.get_requests()
             if r.tmdb_id == tmdb_id and r.media_type == MediaType.TV]
    for goal in goals:
        for dl_id in goal.download_ids:
            dl = storage.get_download(dl_id)
            if dl and dl.status not in (DownloadStatus.ORGANIZED, DownloadStatus.ERROR):
                for s, e in dl.metadata.get("covers") or []:
                    in_flight.add((int(s), int(e)))

    seasons_out = []
    for s in seasons_meta:
        snum, count = s["season_number"], s["episode_count"]
        have = inventory.get(str(snum), {})
        episodes = []
        for e in range(1, count + 1):
            info = have.get(str(e))
            episodes.append({
                "episode": e,
                "have": info is not None,
                "quality": (info or {}).get("quality", ""),
                "in_flight": (snum, e) in in_flight,
            })
        seasons_out.append({
            "season_number": snum,
            "name": s.get("name") or f"Season {snum}",
            "episode_count": count,
            "have_count": len([e for e in episodes if e["have"]]),
            "in_flight_count": len([e for e in episodes if e["in_flight"] and not e["have"]]),
            "episodes": episodes,
        })

    poster = details.get("poster_path")
    backdrop = details.get("backdrop_path")
    return {
        "tmdb_id": tmdb_id,
        "media_type": "tv",
        "title": details.get("name", ""),
        "year": int((details.get("first_air_date") or "0000")[:4] or 0) or None,
        "overview": details.get("overview", ""),
        "rating": details.get("vote_average"),
        "genres": [g["name"] for g in details.get("genres", [])],
        "poster_url": meta_svc.tmdb_image_url(poster, "w500") if poster else (item.poster_path if item else None),
        "backdrop_url": meta_svc.tmdb_image_url(backdrop, "w1280") if backdrop else None,
        "status": details.get("status", ""),
        "in_library": item is not None,
        "library_item_id": item.id if item else None,
        "seasons": seasons_out,
        "goals": [g.to_dict() for g in goals],
        "mandate": mandate.to_dict() if mandate else None,
        "mandate_summary": (mandate.describe() if mandate
                            else "Nothing requested yet."),
    }


@app.get("/api/movies/{tmdb_id}")
async def get_movie(tmdb_id: int):
    """A movie title with its current verified-library state."""
    config = storage.get_config()
    details = await meta_svc.get_movie_details(tmdb_id, config.tmdb_api_key)
    if not details or not details.get("id"):
        raise HTTPException(404, "Movie not found on TMDB")
    item = next(
        (candidate for candidate in storage.get_library()
         if candidate.tmdb_id == tmdb_id and candidate.media_type == MediaType.MOVIE),
        None,
    )
    poster = details.get("poster_path")
    backdrop = details.get("backdrop_path")
    return {
        "tmdb_id": tmdb_id,
        "media_type": "movie",
        "title": details.get("title", ""),
        "year": int((details.get("release_date") or "0000")[:4] or 0) or None,
        "overview": details.get("overview", ""),
        "rating": details.get("vote_average"),
        "genres": [genre["name"] for genre in details.get("genres", [])],
        "poster_url": (meta_svc.tmdb_image_url(poster, "w500") if poster else
                       (item.poster_path if item else None)),
        "backdrop_url": (meta_svc.tmdb_image_url(backdrop, "w1280")
                         if backdrop else None),
        "status": details.get("status", ""),
        "in_library": item is not None and bool(item.metadata.get("verified")),
        "library_item_id": item.id if item else None,
        "seasons": [],
        "goals": [],
    }


@app.get("/api/movies/{tmdb_id}/status")
async def get_movie_status(tmdb_id: int):
    """Whether a movie is in the library / being worked on."""
    item = next((i for i in storage.get_library() if i.tmdb_id == tmdb_id and i.media_type == MediaType.MOVIE), None)
    goals = [r.to_dict() for r in storage.get_requests()
             if r.tmdb_id == tmdb_id and r.media_type == MediaType.MOVIE]
    return {
        "tmdb_id": tmdb_id,
        "in_library": item is not None,
        "library_item_id": item.id if item else None,
        "goals": goals,
    }


@app.post("/api/goals")
async def create_goal(req: CreateGoalRequest):
    """Create a durable goal — the curator takes it from here."""
    if not req.tmdb_id and not (req.query or req.title):
        raise HTTPException(400, "tmdb_id or a query is required")
    config = storage.get_config()
    if config.torrent_client.type == TorrentClientType.NONE:
        raise HTTPException(400, "No torrent client configured. Open Settings first.")
    if not config.staging_dir:
        raise HTTPException(400, "No download folder configured. Open Settings first.")

    media_type = MediaType(req.media_type) if req.media_type in MediaType._value2member_map_ else MediaType.UNKNOWN
    # Resolve unknown type from TMDB when we have an id
    if media_type == MediaType.UNKNOWN and req.tmdb_id and config.tmdb_api_key:
        tv = await meta_svc.get_tv_details(req.tmdb_id, config.tmdb_api_key)
        media_type = MediaType.TV if tv and tv.get("name") else MediaType.MOVIE

    # Don't double-create: an active goal for the same scope is reused
    for existing in storage.get_requests():
        if (existing.tmdb_id and existing.tmdb_id == req.tmdb_id
                and existing.status not in (RequestStatus.FAILED,)
                and existing.media_type == media_type
                and not existing.paused):
            if media_type == MediaType.MOVIE:
                return existing.to_dict()
            wanted_new = set((req.seasons or []))
            wanted_old = {int(s) for s in (existing.wanted_episodes or {})}
            if not wanted_new or wanted_new <= wanted_old:
                return existing.to_dict()

    goal = await curator.create_goal(
        query=req.query,
        tmdb_id=req.tmdb_id,
        media_type=media_type,
        title=req.title,
        seasons=req.seasons,
        episodes=req.episodes,
        quality=req.quality,
        min_quality=req.min_quality,
    )
    return goal.to_dict()


@app.post("/api/goals/{goal_id}/pause")
async def pause_goal(goal_id: str):
    goal = await storage.update_request(goal_id, paused=True)
    if not goal:
        raise HTTPException(404, "Goal not found")
    await broadcast({"type": "request_update", "data": goal.to_dict()})
    return goal.to_dict()


@app.post("/api/goals/{goal_id}/resume")
async def resume_goal(goal_id: str):
    goal = await storage.update_request(goal_id, paused=False, next_check_at=0.0, check_interval=0.0)
    if not goal:
        raise HTTPException(404, "Goal not found")
    if curator:
        curator.poke()
    await broadcast({"type": "request_update", "data": goal.to_dict()})
    return goal.to_dict()


@app.get("/api/activity")
async def get_activity(limit: int = 100, request_id: str = ""):
    return [e.to_dict() for e in storage.get_activity(limit=limit, request_id=request_id)]


# ─── v3: Resolution + Jobs (the agentic system) ───────────────────────────────

@app.get("/api/resolve")
async def resolve_query(q: str):
    """Search box → poster cards. TMDB direct; fuzzy descriptions get one
    cheap LLM call. Cards are annotated with library/job state."""
    config = storage.get_config()
    cards = await resolution.resolve(
        q, config.tmdb_api_key or os.getenv("TMDB_API_KEY", ""),
        config.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", ""),
        agent_service.cheap_model())
    active = {
        (j.media_type, j.tmdb_id): j
        for j in agent_service.store.get_jobs(JobStatus.ACTIVE)
    }
    in_library = {
        (i.media_type.value, i.tmdb_id)
        for i in storage.get_library() if i.tmdb_id
    }
    for c in cards:
        key = (c["media_type"], c["tmdb_id"])
        job = active.get(key)
        c["active_job"] = {"job_id": job.id, "state_line": job.state_line} if job else None
        c["in_library"] = key in in_library
    return cards


class CreateJobRequest(BaseModel):
    model_config = {"extra": "forbid"}
    tmdb_id: int
    media_type: Literal["tv", "movie"] = "tv"
    wanted_episodes: Optional[dict] = None   # explicit episode scope required for TV
    preferred_quality: str = ""
    min_quality: str = ""
    audio_pref: str = "any"
    urgency: str = "soon"
    # Standing authority granted with this request. Empty = leave the show's
    # current monitoring unchanged (new shows default to exact/off).
    monitoring: Literal["", "exact", "keep_current", "seasons", "backfill"] = ""


@app.post("/api/jobs")
async def create_job(req: CreateJobRequest):
    existing = [
        j for j in agent_service.store.get_jobs(JobStatus.ACTIVE)
        if j.tmdb_id == req.tmdb_id and j.media_type == req.media_type
    ]
    if existing:
        raise HTTPException(409, f"There's already an active job for this title "
                                 f"(job {existing[0].id}).")
    try:
        job = await agent_service.create_job(
            tmdb_id=req.tmdb_id, media_type=req.media_type,
            wanted_episodes=req.wanted_episodes,
            preferred_quality=req.preferred_quality, min_quality=req.min_quality,
            audio_pref=req.audio_pref, urgency=req.urgency,
            monitoring=req.monitoring)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return job.to_dict()


class MonitoringRequest(BaseModel):
    mode: Literal["exact", "keep_current", "seasons", "backfill"]
    seasons: list[int] = []
    media_type: Literal["tv", "movie"] = "tv"


@app.get("/api/mandates/{tmdb_id}")
async def get_mandate(tmdb_id: int, media_type: str = "tv"):
    """The user's recorded authority for a title — what agents may acquire."""
    mandate = agent_service.store.get_mandate(tmdb_id, media_type)
    if not mandate:
        return {"tmdb_id": tmdb_id, "media_type": media_type, "mandate": None,
                "summary": "Nothing requested yet."}
    return {"tmdb_id": tmdb_id, "media_type": media_type,
            "mandate": mandate.to_dict(), "summary": mandate.describe()}


@app.put("/api/mandates/{tmdb_id}/monitoring")
async def set_monitoring(tmdb_id: int, req: MonitoringRequest):
    """User-only: change how much a show is monitored. Agents never call this."""
    mandate = agent_service.set_monitoring(
        tmdb_id, req.media_type, req.mode, req.seasons)
    await broadcast({"type": "mandate_update", "data": {
        "tmdb_id": tmdb_id, "media_type": req.media_type,
        "mandate": mandate.to_dict(), "summary": mandate.describe()}})
    return {"mandate": mandate.to_dict(), "summary": mandate.describe()}


@app.get("/api/jobs")
async def list_jobs(status: Optional[str] = None):
    st = JobStatus(status) if status else None
    return [j.to_dict() for j in agent_service.store.get_jobs(st)]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = agent_service.store.get_job(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    session = agent_service.store.get_session(job.session_id) if job.session_id else None
    return {
        "job": job.to_dict(),
        "journal": [e.to_dict() for e in agent_service.store.get_journal(job_id)],
        "session": {
            "status": session.status.value, "outcome": session.outcome.value, "wake_at": session.wake_at,
            "wake_reason": session.wake_reason, "spend": spend_snapshot(session),
        } if session else None,
        "downloads": [d.to_dict() for d in storage.get_all_downloads()
                      if d.metadata.get("job_id") == job_id],
        "activity": [e.to_dict() for e in storage.get_activity(
            limit=100, request_id=job_id)],
        "trace": agent_service.session_trace(session),
    }


@app.post("/api/jobs/{job_id}/nudge")
async def nudge_job(job_id: str):
    if not agent_service.store.get_job(job_id):
        raise HTTPException(404, "No such job")
    await agent_service.nudge(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    job = await agent_service.pause_job(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    job = await agent_service.resume_job(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return job.to_dict()


@app.delete("/api/jobs/{job_id}")
async def cancel_job(job_id: str):
    job = await agent_service.cancel_job(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return job.to_dict()


@app.get("/api/journal")
async def get_journal(job_id: str = "", limit: int = 200):
    """Concise user-facing progress updates, rendered verbatim."""
    return [e.to_dict() for e in agent_service.store.get_journal(job_id, limit)]


@app.get("/api/agent-sessions")
async def list_agent_sessions(open_only: bool = True):
    """Operations console: every session, its state, and its spend."""
    out = []
    for s in agent_service.store.get_sessions(open_only=open_only):
        job = agent_service.store.get_job(s.job_id) if s.job_id else None
        out.append({
            "id": s.id, "agent": s.agent.value, "job_id": s.job_id,
            "job_title": job.title if job else "",
            "download_id": s.download_id, "model": s.model,
            "status": s.status.value, "outcome": s.outcome.value, "wake_at": s.wake_at,
            "wake_reason": s.wake_reason, "spend": spend_snapshot(s),
            "created_at": s.created_at, "updated_at": s.updated_at,
            "closed_at": s.closed_at,
        })
    return out


@app.get("/api/usage")
async def usage_ledger():
    """Exact recorded token usage priced at the list rates stored per call."""
    sessions = []
    by_model: dict[str, dict] = {}
    totals = {"cost": 0.0, "input_tokens": 0, "output_tokens": 0,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
              "api_calls": 0, "legacy_sessions": 0}
    for s in agent_service.store.get_sessions(open_only=False):
        job = agent_service.store.get_job(s.job_id) if s.job_id else None
        spend = spend_snapshot(s)
        row = {
            "id": s.id, "agent": s.agent.value, "job_id": s.job_id,
            "job_title": job.title if job else "", "model": s.model,
            "status": s.status.value, "created_at": s.created_at,
            "updated_at": s.updated_at, "closed_at": s.closed_at,
            "spend": spend,
        }
        sessions.append(row)
        model = by_model.setdefault(s.model, {
            "model": s.model, "cost": 0.0, "input_tokens": 0,
            "output_tokens": 0, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0, "api_calls": 0, "sessions": 0,
            "legacy_sessions": 0,
            "current_rates": rates_for_model(s.model),
        })
        model["sessions"] += 1
        recorded_calls = sum(
            1 for entry in spend["entries"] if not entry.get("legacy_aggregate"))
        legacy_sessions = int(any(
            entry.get("legacy_aggregate") for entry in spend["entries"]))
        for target in (model, totals):
            target["cost"] += spend["dollars"]
            target["input_tokens"] += spend["input_tokens"]
            target["output_tokens"] += spend["output_tokens"]
            target["cache_creation_input_tokens"] += spend["cache_creation_input_tokens"]
            target["cache_read_input_tokens"] += spend["cache_read_input_tokens"]
            target["api_calls"] += recorded_calls
            target["legacy_sessions"] += legacy_sessions
    return {
        "currency": "USD",
        "pricing_source": PRICING_SOURCE,
        "totals": totals,
        "models": sorted(by_model.values(), key=lambda item: item["cost"], reverse=True),
        "sessions": sessions,
    }


@app.get("/api/agent-prompts")
async def agent_prompts():
    return agent_service.prompt_previews()


# ─── Download routes ──────────────────────────────────────────────────────────

@app.get("/api/downloads")
async def list_downloads(status: Optional[str] = None):
    downloads = storage.get_all_downloads()
    if status:
        s = DownloadStatus(status)
        downloads = [d for d in downloads if d.status == s]
    return [d.to_dict() for d in downloads]


@app.post("/api/downloads")
async def add_download(req: AddDownloadRequest):
    config = storage.get_config()

    if config.torrent_client.type == TorrentClientType.NONE:
        raise HTTPException(400, "No torrent client configured. Go to Settings to configure one.")

    if not config.staging_dir:
        raise HTTPException(400, "No staging directory configured.")

    # Extract hash from magnet
    m = re.search(r"xt=urn:btih:([0-9a-fA-F]+)", req.magnet_url, re.IGNORECASE)
    info_hash = m.group(1).lower() if m else str(uuid.uuid4())

    # Check for duplicate
    existing = storage.find_download_by_hash(info_hash)
    if existing:
        return existing.to_dict()

    manager = tc_svc.TorrentManager(config.torrent_client)
    torrent_hash = await manager.add_magnet(req.magnet_url, config.staging_dir)

    # Resolve media_type: use explicit value if set, else inspect catalogue metadata
    requested_type = MediaType(req.media_type) if req.media_type else MediaType.UNKNOWN
    resolved_type = requested_type
    if requested_type == MediaType.UNKNOWN:
        resolved_type = _classify_media_type(req.name or info_hash)

    dl_id = str(uuid.uuid4())
    dl = Download(
        id=dl_id,
        name=req.name or info_hash,
        magnet_url=req.magnet_url,
        media_type=resolved_type,
        status=DownloadStatus.QUEUED,
        torrent_hash=torrent_hash or info_hash,
        tmdb_id=req.tmdb_id,
    )
    await storage.add_download(dl)
    await broadcast({"type": "download_added", "data": dl.to_dict()})

    logging.getLogger("sparrow.downloads").info("Download added", extra={
        "dl_name": dl.name, "hash": dl.torrent_hash,
        "media_type": resolved_type.value, "tmdb_id": req.tmdb_id,
    })
    return dl.to_dict()


@app.get("/api/downloads/{download_id}")
async def get_download(download_id: str):
    dl = storage.get_download(download_id)
    if not dl:
        raise HTTPException(404, "Download not found")
    return dl.to_dict()


@app.delete("/api/downloads/{download_id}")
async def delete_download(download_id: str, delete_files: bool = False):
    dl = storage.get_download(download_id)
    if not dl:
        raise HTTPException(404, "Download not found")

    config = storage.get_config()
    if config.torrent_client.type != TorrentClientType.NONE and dl.torrent_hash:
        manager = tc_svc.TorrentManager(config.torrent_client)
        await manager.delete_torrent(dl.torrent_hash, delete_files=delete_files)

    await storage.delete_download(download_id)
    await broadcast({"type": "download_removed", "data": {"id": download_id}})
    logging.getLogger("sparrow.downloads").info("Download deleted", extra={
        "dl_name": dl.name, "hash": dl.torrent_hash, "delete_files": delete_files,
    })
    return {"success": True}


@app.post("/api/downloads/{download_id}/enrich")
async def enrich_download_metadata(download_id: str):
    """Re-fetch TMDB metadata for a download (useful when type was unknown at completion)."""
    dl = storage.get_download(download_id)
    if not dl:
        raise HTTPException(404, "Download not found")
    config = storage.get_config()
    art_dir = Path(DATA_DIR) / "art"
    art_dir.mkdir(exist_ok=True)
    try:
        meta = await meta_svc.fetch_metadata_for_torrent(
            torrent_name=dl.name,
            media_type=dl.media_type,
            api_key=config.tmdb_api_key,
            art_cache_dir=art_dir,
        )
        await storage.update_download(download_id, metadata=meta, quality=meta.get("quality", dl.quality))
        dl = storage.get_download(download_id)
        await broadcast({"type": "download_update", "data": dl.to_dict()})
        return {"success": True, "poster": meta.get("poster_path", ""), "title": meta.get("title", "")}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/downloads/{download_id}/organize")
async def organize_download(download_id: str, req: OrganizeRequest):
    dl = storage.get_download(download_id)
    if not dl:
        raise HTTPException(404, "Download not found")

    if req.media_type:
        await storage.update_download(download_id, media_type=MediaType(req.media_type))
        dl.media_type = MediaType(req.media_type)

    config = storage.get_config()
    if not config.library_dir:
        raise HTTPException(400, "No library directory configured")

    if req.tmdb_id and req.tmdb_id != dl.tmdb_id:
        art_dir = Path(DATA_DIR) / "art"
        meta = await meta_svc.fetch_metadata_for_torrent(
            dl.name, dl.media_type, config.tmdb_api_key, art_dir, tmdb_id=req.tmdb_id
        )
        await storage.update_download(download_id, metadata=meta, tmdb_id=req.tmdb_id)
        dl.metadata = meta

    asyncio.create_task(auto_organize_download(download_id))
    return {"success": True, "message": "Organization started"}


# ─── Library routes ───────────────────────────────────────────────────────────

@app.get("/api/library")
async def list_library(type: Optional[str] = None):
    media_type = MediaType(type) if type else None
    items = storage.get_library(media_type=media_type)
    return [i.to_dict() for i in items]


@app.get("/api/library/view")
async def library_view():
    """The Library as the user should see it: organized inventory PLUS
    requested/queued/downloading/verifying work from active and paused jobs,
    each title in a plain-language state."""
    return build_library_view(storage, agent_service.store)


@app.get("/api/library/{item_id}")
async def get_library_item(item_id: str):
    item = storage.get_library_item(item_id)
    if not item:
        raise HTTPException(404, "Library item not found")
    return item.to_dict()


@app.delete("/api/library/{item_id}")
async def delete_library_item(item_id: str, delete_files: bool = False):
    item = storage.get_library_item(item_id)
    if not item:
        raise HTTPException(404, "Library item not found")

    if delete_files and item.path:
        import shutil
        shutil.rmtree(item.path, ignore_errors=True)

    await storage.delete_library_item(item_id)
    await broadcast({"type": "library_removed", "data": {"id": item_id}})
    return {"success": True}


@app.post("/api/library/{item_id}/enrich")
async def enrich_library_item(item_id: str):
    """Re-fetch TMDB metadata and artwork for a library item."""
    item = storage.get_library_item(item_id)
    if not item:
        raise HTTPException(404, "Library item not found")
    config = storage.get_config()
    art_dir = Path(DATA_DIR) / "art"
    art_dir.mkdir(exist_ok=True)
    try:
        meta = await meta_svc.fetch_metadata_for_torrent(
            torrent_name=item.title,
            media_type=item.media_type,
            api_key=config.tmdb_api_key,
            art_cache_dir=art_dir,
            tmdb_id=item.tmdb_id,
        )
        updates: dict = {"metadata": {**item.metadata, **meta}}
        if meta.get("poster_path"):
            updates["poster_path"] = meta["poster_path"]
        if meta.get("backdrop_path"):
            updates["backdrop_path"] = meta["backdrop_path"]
        if meta.get("tmdb_id"):
            updates["tmdb_id"] = meta["tmdb_id"]
        if meta.get("overview"):
            updates["overview"] = meta["overview"]
        if meta.get("rating"):
            updates["rating"] = meta["rating"]
        if meta.get("genres"):
            updates["genres"] = meta["genres"]
        if item.media_type == MediaType.UNKNOWN:
            if "/Movies/" in item.path or "\\Movies\\" in item.path:
                updates["media_type"] = MediaType.MOVIE
            elif "/TV Shows/" in item.path or "\\TV Shows\\" in item.path:
                updates["media_type"] = MediaType.TV
        refreshed = await storage.update_library_item(item_id, **updates)
        if refreshed:
            await broadcast({"type": "library_update", "data": refreshed.to_dict()})
        return {"success": True, "poster": meta.get("poster_path", "")}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/library/scan")
async def scan_library():
    config = storage.get_config()
    if not config.library_dir:
        raise HTTPException(400, "No library directory configured")

    items = file_organizer.scan_library(config.library_dir)
    added = 0
    refreshed = 0
    for item_data in items:
        # Already known: refresh the on-disk episode inventory
        existing = [i for i in storage.get_library() if i.path == item_data["path"]]
        if existing:
            item = existing[0]
            disk_eps = item_data.get("episodes") or {}
            if item.media_type == MediaType.TV and disk_eps != item.episodes:
                merged = {season: dict(episodes) for season, episodes in item.episodes.items()}
                for season, episodes in disk_eps.items():
                    for episode, record in episodes.items():
                        old = item.episodes.get(season, {}).get(episode)
                        merged.setdefault(season, {})[episode] = old if old and old.get("path") == record.get("path") else record
                await storage.update_library_item(item.id, episodes=merged)
                refreshed += 1
            continue

        art_dir = Path(DATA_DIR) / "art"
        meta = await meta_svc.fetch_metadata_for_torrent(
            torrent_name=item_data["title"],
            media_type=MediaType(item_data["media_type"]),
            api_key=config.tmdb_api_key,
            art_cache_dir=art_dir,
        )

        item = LibraryItem(
            id=str(uuid.uuid4()),
            title=meta.get("title", item_data["title"]),
            media_type=MediaType(item_data["media_type"]),
            path=item_data["path"],
            year=meta.get("year") or item_data.get("year"),
            tmdb_id=meta.get("tmdb_id"),
            imdb_id=meta.get("imdb_id"),
            overview=meta.get("overview", ""),
            poster_path=meta.get("poster_path", ""),
            backdrop_path=meta.get("backdrop_path", ""),
            genres=meta.get("genres", []),
            rating=meta.get("rating"),
            seasons=meta.get("seasons"),
            episode_count=meta.get("episode_count"),
            metadata=meta,
            episodes=item_data.get("episodes") or {},
        )
        await storage.add_library_item(item)
        added += 1

    if curator:
        curator.poke()
    return {"scanned": len(items), "added": added, "refreshed": refreshed}


# ─── Metadata routes ──────────────────────────────────────────────────────────

@app.get("/api/metadata/search")
async def search_metadata(q: str, type: str = "movie"):
    config = storage.get_config()
    media_type = MediaType(type)
    results = await meta_svc.search_tmdb(q, media_type, config.tmdb_api_key)
    return results[:10]


@app.get("/api/metadata/{tmdb_id}")
async def get_metadata(tmdb_id: int, type: str = "movie"):
    config = storage.get_config()
    media_type = MediaType(type)
    if media_type == MediaType.MOVIE:
        data = await meta_svc.get_movie_details(tmdb_id, config.tmdb_api_key)
    else:
        data = await meta_svc.get_tv_details(tmdb_id, config.tmdb_api_key)
    if not data:
        raise HTTPException(404, "Not found on TMDB")
    return data


# ─── Log routes ───────────────────────────────────────────────────────────────

@app.get("/api/logs")
async def get_logs(limit: int = 200, level: str = ""):
    """Recent structured log entries."""
    log_file = Path(DATA_DIR) / "logs" / "sparrow.log"
    if not log_file.exists():
        return []
    lines = _tail_log_lines(log_file, limit * 3)
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if level and entry.get("level", "").upper() != level.upper():
                continue
            entries.append(entry)
        except Exception:
            pass
    return entries[-limit:]


@app.get("/api/logs/stream")
async def stream_logs():
    """SSE tail of the live log file."""
    log_file = Path(DATA_DIR) / "logs" / "sparrow.log"

    async def generator():
        size = log_file.stat().st_size if log_file.exists() else 0
        while True:
            await asyncio.sleep(1)
            if not log_file.exists():
                continue
            try:
                new_size = log_file.stat().st_size
            except Exception:
                continue
            if new_size > size:
                try:
                    with open(log_file, "rb") as f:
                        f.seek(size)
                        chunk = f.read(new_size - size).decode("utf-8", errors="replace")
                    size = new_size
                    for line in chunk.split("\n"):
                        line = line.strip()
                        if line:
                            yield f"data: {line}\n\n"
                except Exception:
                    pass
            elif new_size < size:
                size = new_size  # file rotated

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Product routes are registered before the SPA fallback.
from .agents.account_api import install_accounts, COOKIE
accounts = install_accounts(app, storage, lambda: agent_service)
from .agents.nodes import install_nodes
nodes = install_nodes(app, storage, lambda: agent_service)
from .agents.product_api import install_product
catalogue = install_product(app, storage, accounts, nodes, lambda: agent_service)
from .agents.subtitles import install_subtitles
from .agents.curation import install_curation
install_curation(app, accounts, lambda: agent_service)
subtitles = install_subtitles(app, accounts, nodes, catalogue, lambda: agent_service)
from .agents.playback import install_playback
playback = install_playback(app, storage, accounts, nodes, catalogue)
from .agents.discovery import install_discovery
discovery = install_discovery(app, accounts, catalogue, lambda: agent_service)

# ─── Static file serving ──────────────────────────────────────────────────────

# Serve cached artwork
art_dir = Path(DATA_DIR) / "art"
art_dir.mkdir(parents=True, exist_ok=True)
app.mount("/art", StaticFiles(directory=str(art_dir)), name="art")

# Serve frontend build (if present)
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith(('api/','art/')): raise HTTPException(404,'Not found.')
        if full_path in {'sw.js','manifest.webmanifest','icon.svg'}:
            file=frontend_dist/full_path
            if not file.is_file(): raise HTTPException(404,'Not found.')
            media={'sw.js':'application/javascript','manifest.webmanifest':'application/manifest+json','icon.svg':'image/svg+xml'}[full_path]
            return FileResponse(file,media_type=media,headers={'Cache-Control':'no-cache'})
        index = frontend_dist / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(404, "Frontend not built. Run: cd frontend && npm run build")
else:
    @app.get("/")
    async def root():
        return {"message": "Sparrow API running. Build frontend with: cd frontend && npm run build"}


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("SPARROW_HOST", "127.0.0.1")
    port = int(os.getenv("SPARROW_PORT", "8888"))
    validate_bind(host)
    uvicorn.run("backend.main:app", host=host, port=port,
                reload=env_bool("SPARROW_RELOAD"))
