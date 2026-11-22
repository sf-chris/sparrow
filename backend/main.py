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

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
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
    Download, SparrowConfig, TorrentClientConfig,
    MediaType, DownloadStatus, TorrentClientType,
)
from .services import torrent_client as tc_svc
from .services import metadata_service as meta_svc
from .services import release_parser
from .services.library_view import build_library_view
from .agents.service import AgentService
from .agents.models import JobStatus
from .agents.runtime import PRICING_SOURCE, rates_for_model, spend_snapshot
from . import __version__
from .configuration import (
    apply_config_update, effective_provider_key, effective_tmdb_key, public_config,
    repair_media_folders, track_saved_config,
)
from .doctor import build_report as build_doctor_report
from .runtime_settings import (
    configured_origins, env_bool, validate_bind, websocket_origin_allowed,
)


DATA_DIR = str(Path(os.getenv("SPARROW_DATA_DIR", "./data")).expanduser().resolve(strict=False))
storage = Storage(DATA_DIR)
track_saved_config(storage.get_config)
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
    global agent_service
    _setup_logging(DATA_DIR)
    await storage.load_all()
    # Folders typed as host paths (/home/me/Media/...) live at their Docker mount.
    repaired = SparrowConfig.from_dict(storage.get_config().to_dict())
    try:
        if repair_media_folders(repaired):
            await storage.save_config(repaired)
    except ValueError:
        pass
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
    moves_task = asyncio.create_task(moves.loop())
    yield
    await subtitles.stop()
    migration_task.cancel()
    moves_task.cancel()
    await agent_service.shutdown()
    await nodes.local().shutdown()
    downloader_task.cancel()
    await managed_transmission.shutdown()
    task1.cancel()
    task2.cancel()
    task3.cancel()


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
                    # enrich_download would clobber the job metadata.
                    if not dl.metadata.get("agent_managed"):
                        asyncio.create_task(enrich_download(dl.id))

                await storage.update_download(dl.id, **updates)
                await broadcast({"type": "download_update", "data": {**dl.to_dict(), **updates, "status": new_status.value}})

        except asyncio.CancelledError:
            break
        except Exception:
            pass


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
    extra_library_dirs: Optional[list[str]] = Field(default=None, max_length=16)
    torrent_client: Optional[dict] = None
    quality_preference: Optional[str] = None
    tmdb_api_key: Optional[str] = None
    provider_api_key: Optional[str] = None
    altprovider_api_key: Optional[str] = None
    onboarding_complete: Optional[bool] = None
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
    clear_provider_api_key: bool = False
    clear_altprovider_api_key: bool = False


class AddDownloadRequest(BaseModel):
    magnet_url: str
    name: str = ""
    media_type: str = "unknown"
    tmdb_id: Optional[int] = None


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
        "has_provider_key": bool(effective_provider_key(config)),
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

    if not effective_provider_key(config):
        issues.append({
            "id": "provider_missing",
            "title": "Reasoning key is missing",
            "detail": "Fetch, Media, and Librarian agents need an Provider API key.",
            "actions": ["Add Provider key"],
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


# ─── Activity ──────────────────────────────────────────────────────────────────

@app.get("/api/activity")
async def get_activity(limit: int = 100, request_id: str = ""):
    return [e.to_dict() for e in storage.get_activity(limit=limit, request_id=request_id)]


# ─── v3: Resolution + Jobs (the agentic system) ───────────────────────────────

@app.get("/api/resolve")
async def resolve_query(q: str):
    """Direct TMDB title lookup → poster cards annotated with library/job
    state. No model call: descriptions belong to the Discovery agent."""
    cards = await meta_svc.tmdb_quick_suggest(q, effective_tmdb_key(storage.get_config()))
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
from .agents.placement import install_placement
placement = install_placement(app, accounts, nodes, storage, lambda: agent_service)
from .agents.moves import install_moves
moves = install_moves(app, accounts, nodes, storage, catalogue, placement, lambda: agent_service)
from .agents.subtitles import install_subtitles
from .agents.curation import install_curation
install_curation(app, accounts, lambda: agent_service)
subtitles = install_subtitles(app, accounts, nodes, catalogue, lambda: agent_service)
from .agents.playback import install_playback
playback = install_playback(app, storage, accounts, nodes, catalogue)
from .agents.discovery import install_discovery
discovery = install_discovery(app, accounts, catalogue, lambda: agent_service)
from .agents.ledger import install_ledger
install_ledger(app, lambda: agent_service)

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
