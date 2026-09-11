"""
Filesystem-backed storage layer. Everything lives in the data/ directory as
JSON files (config, downloads, library) — no external database required.
"""
from __future__ import annotations
import json
import os
import uuid
import time
import asyncio
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from .agents.migrations import prepare, atomic_text

from .models import (
    SparrowConfig, Download, LibraryItem, CWMLog, MediaRequest, ActivityEvent,
    TorrentClientConfig, TorrentClientType, Quality, MediaType, DownloadStatus
)


class Storage:
    """All persistent state for Sparrow, backed by JSON files on disk."""

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir).expanduser().resolve(strict=False)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        prepare(self.data_dir)
        from .agents.operations import Operations
        self.operations = Operations(self.data_dir)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass
        (self.data_dir / "art").mkdir(exist_ok=True)

        self._config_path = self.data_dir / "config.json"
        self._downloads_path = self.data_dir / "downloads.json"
        self._library_path = self.data_dir / "library.json"
        self._cwm_logs_path = self.data_dir / "cwm_logs.json"
        self._requests_path = self.data_dir / "requests.json"
        self._db_path = self.data_dir / "sparrow.db"

        self._activity_path = self.data_dir / "activity.json"

        self._lock = asyncio.Lock()
        self._downloads: dict[str, Download] = {}
        self._library: dict[str, LibraryItem] = {}
        self._requests: dict[str, MediaRequest] = {}
        self._cwm_logs: list[CWMLog] = []
        self._activity: list[ActivityEvent] = []
        self._config: Optional[SparrowConfig] = None

    async def load_all(self) -> None:
        """Load all data from disk into memory."""
        self._init_db()
        stored_config = self._load_config_sqlite()
        established = stored_config is not None
        self._config = stored_config if established else self._load_config()
        # An empty established table is intentional. A stale JSON mirror must
        # never resurrect removed records or replace a corrupt SQLite database.
        self._downloads = self._load_downloads_sqlite() if established else self._load_downloads()
        self._library = self._load_library_sqlite() if established else self._load_library()
        self._requests = self._load_requests_sqlite() if established else self._load_requests()
        self._cwm_logs = self._load_cwm_logs_sqlite() if established else self._load_cwm_logs()
        self._activity = self._load_activity(sqlite_only=established)
        if not established:
            # Commit imported tables first; config is the final migration marker.
            # A crash before that marker repeats import from the untouched JSON.
            await self._save_downloads_sqlite()
            await self._save_library_sqlite()
            await self._save_requests_sqlite()
            await self._save_cwm_logs_sqlite()
            await self._save_config_sqlite(self._config)

    # ─── SQLite ──────────────────────────────────────────────────────────

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn: yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    added_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS library_items (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    title TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    added_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS media_requests (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    status TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS request_downloads (
                    request_id TEXT NOT NULL,
                    download_id TEXT NOT NULL,
                    PRIMARY KEY (request_id, download_id)
                );
                CREATE TABLE IF NOT EXISTS cwm_logs (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity_events (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS parsed_releases (
                    name_hash TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    data TEXT NOT NULL,
                    parsed_by TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)

    def _load_config_sqlite(self) -> Optional[SparrowConfig]:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT data FROM config WHERE id = 1").fetchone()
            return SparrowConfig.from_dict(json.loads(row["data"])) if row else None
        except Exception as exc:
            raise ValueError('Could not load stored state; the database has been preserved for recovery.') from exc

    async def _save_config_sqlite(self, config: SparrowConfig) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO config (id, data) VALUES (1, ?)",
                (json.dumps(config.to_dict()),),
            )

    def _load_downloads_sqlite(self) -> dict[str, Download]:
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT data FROM downloads").fetchall()
            return {d.id: d for d in (Download.from_dict(json.loads(r["data"])) for r in rows)}
        except Exception as exc:
            raise ValueError('Could not load stored state; the database has been preserved for recovery.') from exc

    async def _save_downloads_sqlite(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM downloads")
            conn.executemany(
                "INSERT INTO downloads (id, data, added_at) VALUES (?, ?, ?)",
                [(d.id, json.dumps(d.to_dict()), d.added_at) for d in self._downloads.values()],
            )

    def _load_library_sqlite(self) -> dict[str, LibraryItem]:
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT data FROM library_items").fetchall()
            return {i.id: i for i in (LibraryItem.from_dict(json.loads(r["data"])) for r in rows)}
        except Exception as exc:
            raise ValueError('Could not load stored state; the database has been preserved for recovery.') from exc

    async def _save_library_sqlite(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM library_items")
            conn.executemany(
                "INSERT INTO library_items (id, data, title, media_type, added_at) VALUES (?, ?, ?, ?, ?)",
                [(i.id, json.dumps(i.to_dict()), i.title, i.media_type.value, i.added_at) for i in self._library.values()],
            )

    def _load_requests_sqlite(self) -> dict[str, MediaRequest]:
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT data FROM media_requests").fetchall()
            return {r.id: r for r in (MediaRequest.from_dict(json.loads(row["data"])) for row in rows)}
        except Exception as exc:
            raise ValueError('Could not load stored state; the database has been preserved for recovery.') from exc

    async def _save_requests_sqlite(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM media_requests")
            conn.execute("DELETE FROM request_downloads")
            conn.executemany(
                "INSERT INTO media_requests (id, data, status, strategy, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (r.id, json.dumps(r.to_dict()), r.status.value, r.strategy.value, r.created_at, r.updated_at)
                    for r in self._requests.values()
                ],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO request_downloads (request_id, download_id) VALUES (?, ?)",
                [(r.id, dl_id) for r in self._requests.values() for dl_id in r.download_ids],
            )

    def _load_cwm_logs_sqlite(self) -> list[CWMLog]:
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT data FROM cwm_logs ORDER BY timestamp ASC").fetchall()
            return [CWMLog(**json.loads(r["data"])) for r in rows]
        except Exception as exc:
            raise ValueError('Could not load stored state; the database has been preserved for recovery.') from exc

    async def _save_cwm_logs_sqlite(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM cwm_logs")
            conn.executemany(
                "INSERT INTO cwm_logs (id, data, timestamp) VALUES (?, ?, ?)",
                [(l.id, json.dumps(l.to_dict()), l.timestamp) for l in self._cwm_logs],
            )

    # ─── Config ──────────────────────────────────────────────────────────

    def _load_config(self) -> SparrowConfig:
        if self._config_path.exists():
            try:
                return SparrowConfig.from_dict(json.loads(self._config_path.read_text()))
            except Exception as exc:
                raise ValueError("Cannot migrate malformed config.json; original data was preserved.") from exc
        return SparrowConfig()

    def get_config(self) -> SparrowConfig:
        if self._config is None:
            self._config = self._load_config()
        return self._config

    async def save_config(self, config: SparrowConfig) -> None:
        async with self._lock:
            self._config = config
            await self._save_config_sqlite(config)
            atomic_text(self._config_path, json.dumps(config.to_dict(), indent=2))
            try:
                self._config_path.chmod(0o600)
                self._db_path.chmod(0o600)
            except OSError:
                pass

    # ─── Downloads ───────────────────────────────────────────────────────

    def _load_downloads(self) -> dict[str, Download]:
        if self._downloads_path.exists():
            try:
                raw = json.loads(self._downloads_path.read_text())
                return {d["id"]: Download.from_dict(d) for d in raw}
            except Exception as exc:
                raise ValueError("Cannot migrate malformed downloads.json; original data was preserved.") from exc
        return {}

    async def _save_downloads(self) -> None:
        data = [d.to_dict() for d in self._downloads.values()]
        await self._save_downloads_sqlite()
        atomic_text(self._downloads_path, json.dumps(data, indent=2))

    async def add_download(self, download: Download) -> Download:
        async with self._lock:
            self._downloads[download.id] = download
            await self._save_downloads()
            self.operations.download(download)
        return download

    async def update_download(self, download_id: str, **kwargs) -> Optional[Download]:
        async with self._lock:
            if download_id not in self._downloads:
                return None
            dl = self._downloads[download_id]
            for k, v in kwargs.items():
                if hasattr(dl, k):
                    setattr(dl, k, v)
            await self._save_downloads()
            self.operations.download(dl)
            return dl

    async def delete_download(self, download_id: str) -> bool:
        async with self._lock:
            if download_id not in self._downloads:
                return False
            del self._downloads[download_id]
            await self._save_downloads()
            return True

    def get_download(self, download_id: str) -> Optional[Download]:
        return self._downloads.get(download_id)

    def get_all_downloads(self) -> list[Download]:
        return sorted(self._downloads.values(), key=lambda d: d.added_at, reverse=True)

    def find_download_by_hash(self, torrent_hash: str) -> Optional[Download]:
        for dl in self._downloads.values():
            if dl.torrent_hash.lower() == torrent_hash.lower():
                return dl
        return None

    # ─── Library ─────────────────────────────────────────────────────────

    def _load_library(self) -> dict[str, LibraryItem]:
        if self._library_path.exists():
            try:
                raw = json.loads(self._library_path.read_text())
                return {i["id"]: LibraryItem.from_dict(i) for i in raw}
            except Exception as exc:
                raise ValueError("Cannot migrate malformed library.json; original data was preserved.") from exc
        return {}

    async def _save_library(self) -> None:
        data = [i.to_dict() for i in self._library.values()]
        await self._save_library_sqlite()
        atomic_text(self._library_path, json.dumps(data, indent=2))

    # ─── Requests ────────────────────────────────────────────────────────

    def _load_requests(self) -> dict[str, MediaRequest]:
        if self._requests_path.exists():
            try:
                raw = json.loads(self._requests_path.read_text())
                return {r["id"]: MediaRequest.from_dict(r) for r in raw}
            except Exception as exc:
                raise ValueError("Cannot migrate malformed requests.json; original data was preserved.") from exc
        return {}

    async def _save_requests(self) -> None:
        data = [r.to_dict() for r in self._requests.values()]
        await self._save_requests_sqlite()
        atomic_text(self._requests_path, json.dumps(data, indent=2))

    async def add_request(self, request: MediaRequest) -> MediaRequest:
        async with self._lock:
            self._requests[request.id] = request
            await self._save_requests()
        return request

    async def update_request(self, request_id: str, **kwargs) -> Optional[MediaRequest]:
        async with self._lock:
            if request_id not in self._requests:
                return None
            req = self._requests[request_id]
            for k, v in kwargs.items():
                if hasattr(req, k):
                    setattr(req, k, v)
            req.updated_at = time.time()
            await self._save_requests()
            return req

    async def link_request_download(self, request_id: str, download_id: str) -> Optional[MediaRequest]:
        async with self._lock:
            req = self._requests.get(request_id)
            if not req:
                return None
            if download_id not in req.download_ids:
                req.download_ids.append(download_id)
            req.updated_at = time.time()
            await self._save_requests()
            return req

    async def delete_request(self, request_id: str) -> bool:
        async with self._lock:
            if request_id not in self._requests:
                return False
            del self._requests[request_id]
            await self._save_requests()
            return True

    def get_request(self, request_id: str) -> Optional[MediaRequest]:
        return self._requests.get(request_id)

    def get_requests(self) -> list[MediaRequest]:
        return sorted(self._requests.values(), key=lambda r: r.created_at, reverse=True)

    async def add_library_item(self, item: LibraryItem) -> LibraryItem:
        async with self._lock:
            self._library[item.id] = item
            await self._save_library()
        return item

    async def update_library_item(self, item_id: str, **kwargs) -> Optional[LibraryItem]:
        async with self._lock:
            if item_id not in self._library:
                return None
            item = self._library[item_id]
            for k, v in kwargs.items():
                if hasattr(item, k):
                    setattr(item, k, v)
            await self._save_library()
            return item

    async def delete_library_item(self, item_id: str) -> bool:
        async with self._lock:
            if item_id not in self._library:
                return False
            del self._library[item_id]
            await self._save_library()
            return True

    def get_library_item(self, item_id: str) -> Optional[LibraryItem]:
        return self._library.get(item_id)

    def get_library(self, media_type: Optional[MediaType] = None) -> list[LibraryItem]:
        items = list(self._library.values())
        if media_type:
            items = [i for i in items if i.media_type == media_type]
        return sorted(items, key=lambda i: i.title.lower())

    # ─── CWM Logs ────────────────────────────────────────────────────────

    def _load_cwm_logs(self) -> list[CWMLog]:
        if self._cwm_logs_path.exists():
            try:
                raw = json.loads(self._cwm_logs_path.read_text())
                return [CWMLog(**l) for l in raw]
            except Exception as exc:
                raise ValueError("Cannot migrate malformed cwm_logs.json; original data was preserved.") from exc
        return []


    # ─── Activity Feed ───────────────────────────────────────────────────

    def _load_activity(self, sqlite_only=False) -> list[ActivityEvent]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT data FROM activity_events ORDER BY timestamp ASC"
                ).fetchall()
            if rows or sqlite_only:
                return [ActivityEvent(**json.loads(r["data"])) for r in rows]
        except Exception:
            if sqlite_only: raise
        if self._activity_path.exists():
            try:
                raw = json.loads(self._activity_path.read_text())
                return [ActivityEvent(**e) for e in raw]
            except Exception:
                pass
        return []

    async def add_activity(
        self,
        kind: str,
        message: str,
        detail: str = "",
        request_id: str = "",
        tmdb_id: Optional[int] = None,
        level: str = "info",
    ) -> ActivityEvent:
        event = ActivityEvent(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            kind=kind,
            message=message,
            detail=detail,
            request_id=request_id,
            tmdb_id=tmdb_id,
            level=level,
        )
        async with self._lock:
            self._activity.append(event)
            self._activity = self._activity[-1000:]
            with self._connect() as conn:
                conn.execute("DELETE FROM activity_events")
                conn.executemany(
                    "INSERT INTO activity_events (id, data, timestamp) VALUES (?, ?, ?)",
                    [(e.id, json.dumps(e.to_dict()), e.timestamp) for e in self._activity],
                )
            atomic_text(self._activity_path,
                json.dumps([e.to_dict() for e in self._activity], indent=2)
            )
        return event

    def get_activity(self, limit: int = 100, request_id: str = "") -> list[ActivityEvent]:
        events = self._activity
        if request_id:
            events = [e for e in events if e.request_id == request_id]
        return list(reversed(events[-limit:]))

    # ─── Parsed Release Cache ────────────────────────────────────────────

    def get_parsed_release(self, name_hash: str) -> Optional[dict]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT data FROM parsed_releases WHERE name_hash = ?", (name_hash,)
                ).fetchone()
            return json.loads(row["data"]) if row else None
        except Exception:
            return None

    def put_parsed_release(self, name_hash: str, name: str, data: dict, parsed_by: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO parsed_releases "
                    "(name_hash, name, data, parsed_by, created_at) VALUES (?, ?, ?, ?, ?)",
                    (name_hash, name, json.dumps(data), parsed_by, time.time()),
                )
        except Exception:
            pass

    # ─── Art Cache ───────────────────────────────────────────────────────

    def art_path(self, tmdb_id: int, art_type: str, ext: str = "jpg") -> Path:
        return self.data_dir / "art" / f"{art_type}_{tmdb_id}.{ext}"

    async def save_art(self, tmdb_id: int, art_type: str, data: bytes, ext: str = "jpg") -> str:
        path = self.art_path(tmdb_id, art_type, ext)
        path.write_bytes(data)
        return str(path)

    def art_url(self, tmdb_id: int, art_type: str, ext: str = "jpg") -> Optional[str]:
        path = self.art_path(tmdb_id, art_type, ext)
        if path.exists():
            return f"/art/{art_type}_{tmdb_id}.{ext}"
        return None
