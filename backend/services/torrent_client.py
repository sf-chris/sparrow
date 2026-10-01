"""
Torrent client integration — discovers and interfaces with qBittorrent or Transmission.

Discovery probes common ports; once connected it translates all operations into
a uniform API so the rest of the system doesn't care which client is running.
"""
from __future__ import annotations
import asyncio
import shutil
import sys
import urllib.parse
from pathlib import Path
from typing import Optional
import httpx

from ..models import TorrentClientConfig, TorrentClientInfo, TorrentClientType, DownloadStatus


TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://9.rarbg.to:2710/announce",
    "udp://tracker.leechers-paradise.org:6969/announce",
    "udp://exodus.desync.com:6969/announce",
]


def build_magnet(info_hash: str, name: str) -> str:
    tr_params = "&".join(f"tr={urllib.parse.quote(t)}" for t in TRACKERS)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(name)}&{tr_params}"


class TorrentClientError(Exception):
    pass


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


async def start_configured_client(config: TorrentClientConfig) -> tuple[bool, str]:
    """Start an installed local client without hiding installation/config errors.

    Starting an existing application is safe recovery. Installing software or
    changing its remote-access settings still requires an explicit user action.
    """
    if config.type == TorrentClientType.NONE:
        return False, "No download app is configured."
    if config.host.strip().lower() not in LOCAL_HOSTS:
        return False, "The configured download app is on another machine, so Sparrow cannot start it."

    display_name = (
        "Transmission" if config.type == TorrentClientType.TRANSMISSION
        else "qBittorrent"
    )

    if sys.platform == "darwin":
        app_name = f"{display_name}.app"
        candidates = [
            Path("/Applications") / app_name,
            Path.home() / "Applications" / app_name,
        ]
        app = next((path for path in candidates if path.exists()), None)
        if not app:
            return False, f"{display_name} is configured but is not installed."
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/open", str(app),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if await proc.wait() == 0:
            return True, f"Started {display_name}."
        return False, f"Found {display_name}, but macOS could not start it."

    commands = (
        ("transmission-daemon", "transmission-gtk")
        if config.type == TorrentClientType.TRANSMISSION
        else ("qbittorrent", "qbittorrent-nox")
    )
    executable = next((shutil.which(command) for command in commands if shutil.which(command)), None)
    if not executable:
        return False, f"{display_name} is configured but is not installed."
    args = [executable]
    if Path(executable).name == "transmission-daemon":
        args.extend(["--rpc-port", str(config.port)])
    elif Path(executable).name == "qbittorrent-nox":
        args.append(f"--webui-port={config.port}")
    await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    return True, f"Started {display_name}."


# ─── qBittorrent ─────────────────────────────────────────────────────────────

class QBittorrentClient:
    def __init__(self, host: str, port: int, username: str = "admin", password: str = "adminadmin"):
        self.base_url = f"http://{host}:{port}"
        self.username = username
        self.password = password
        self._session_cookie: Optional[str] = None

    async def login(self) -> bool:
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/api/v2/auth/login",
                    data={"username": self.username, "password": self.password},
                )
                if resp.text == "Ok.":
                    self._session_cookie = resp.cookies.get("SID", "")
                    return True
                return False
            except Exception:
                return False

    @property
    def _cookies(self) -> dict:
        return {"SID": self._session_cookie} if self._session_cookie else {}

    async def get_version(self) -> str:
        async with httpx.AsyncClient(timeout=5.0, cookies=self._cookies) as client:
            resp = await client.get(f"{self.base_url}/api/v2/app/version")
            return resp.text.strip()

    async def add_magnet(self, magnet_url: str, save_path: str) -> bool:
        async with httpx.AsyncClient(timeout=10.0, cookies=self._cookies) as client:
            resp = await client.post(
                f"{self.base_url}/api/v2/torrents/add",
                data={"urls": magnet_url, "savepath": save_path},
            )
            return resp.text == "Ok."

    async def get_torrents(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=10.0, cookies=self._cookies) as client:
            resp = await client.get(f"{self.base_url}/api/v2/torrents/info?filter=all")
            return resp.json()

    async def get_files(self, torrent_hash: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=10.0, cookies=self._cookies) as client:
            resp = await client.get(f"{self.base_url}/api/v2/torrents/files", params={"hash": torrent_hash})
            if resp.status_code != 200:
                return []
            return [{"name": f.get("name", ""), "size": f.get("size", 0)} for f in resp.json()]

    async def skip_files(self, torrent_hash: str, indices: list[int]) -> bool:
        async with httpx.AsyncClient(timeout=10.0, cookies=self._cookies) as client:
            resp = await client.post(
                f"{self.base_url}/api/v2/torrents/filePrio",
                data={"hash": torrent_hash, "id": "|".join(map(str, indices)), "priority": 0},
            )
            return resp.status_code == 200

    async def get_torrent(self, torrent_hash: str) -> Optional[dict]:
        torrents = await self.get_torrents()
        for t in torrents:
            if t.get("hash", "").lower() == torrent_hash.lower():
                return t
        return None

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        async with httpx.AsyncClient(timeout=5.0, cookies=self._cookies) as client:
            resp = await client.post(
                f"{self.base_url}/api/v2/torrents/delete",
                data={"hashes": torrent_hash, "deleteFiles": str(delete_files).lower()},
            )
            return resp.status_code == 200

    async def stop_torrent(self, torrent_hash: str) -> bool:
        async with httpx.AsyncClient(timeout=5.0, cookies=self._cookies) as client:
            resp = await client.post(
                f"{self.base_url}/api/v2/torrents/pause",
                data={"hashes": torrent_hash},
            )
            return resp.status_code == 200

    async def start_torrent(self, torrent_hash: str) -> bool:
        async with httpx.AsyncClient(timeout=5.0, cookies=self._cookies) as client:
            resp = await client.post(
                f"{self.base_url}/api/v2/torrents/resume",
                data={"hashes": torrent_hash},
            )
            return resp.status_code == 200

    def map_status(self, qbt_state: str) -> DownloadStatus:
        mapping = {
            "downloading": DownloadStatus.DOWNLOADING,
            "stalledDL": DownloadStatus.DOWNLOADING,
            "metaDL": DownloadStatus.DOWNLOADING,
            "checkingResumeData": DownloadStatus.DOWNLOADING,
            "uploading": DownloadStatus.SEEDING,
            "stalledUP": DownloadStatus.SEEDING,
            "seeding": DownloadStatus.SEEDING,
            "queuedDL": DownloadStatus.QUEUED,
            "queuedUP": DownloadStatus.QUEUED,
            "pausedDL": DownloadStatus.PAUSED,
            "pausedUP": DownloadStatus.PAUSED,
            "error": DownloadStatus.ERROR,
            "missingFiles": DownloadStatus.ERROR,
        }
        return mapping.get(qbt_state, DownloadStatus.DOWNLOADING)


# ─── Transmission ────────────────────────────────────────────────────────────

class TransmissionClient:
    def __init__(self, host: str, port: int, username: str = "", password: str = ""):
        self.base_url = f"http://{host}:{port}/transmission/rpc"
        self.username = username
        self.password = password
        self._session_id: str = ""

    def _auth(self) -> Optional[tuple]:
        if self.username:
            return (self.username, self.password)
        return None

    async def _rpc(self, method: str, arguments: dict = None) -> dict:
        payload = {"method": method, "arguments": arguments or {}}
        async with httpx.AsyncClient(timeout=10.0) as client:
            for _ in range(2):
                headers = {"X-Transmission-Session-Id": self._session_id}
                try:
                    resp = await client.post(
                        self.base_url,
                        json=payload,
                        headers=headers,
                        auth=self._auth(),
                    )
                    if resp.status_code == 409:
                        self._session_id = resp.headers.get("X-Transmission-Session-Id", "")
                        continue
                    return resp.json()
                except Exception as e:
                    raise TorrentClientError(str(e))
        return {}

    async def get_version(self) -> str:
        result = await self._rpc("session-get")
        return result.get("arguments", {}).get("version", "unknown")

    async def add_magnet(self, magnet_url: str, save_path: str) -> str:
        result = await self._rpc("torrent-add", {"filename": magnet_url, "download-dir": save_path})
        added = result.get("arguments", {}).get("torrent-added", {})
        return added.get("hashString", "")

    async def get_torrents(self) -> list[dict]:
        fields = ["id", "name", "hashString", "status", "percentDone",
                  "rateDownload", "rateUpload", "eta", "totalSize", "sizeWhenDone", "downloadedEver",
                  "uploadedEver", "secondsSeeding", "uploadRatio",
                  "downloadDir", "error", "errorString"]
        result = await self._rpc("torrent-get", {"fields": fields})
        return result.get("arguments", {}).get("torrents", [])

    async def get_torrent(self, torrent_hash: str) -> Optional[dict]:
        torrents = await self.get_torrents()
        for t in torrents:
            if t.get("hashString", "").lower() == torrent_hash.lower():
                return t
        return None

    async def get_files(self, torrent_hash: str) -> list[dict]:
        result = await self._rpc("torrent-get", {"ids": [torrent_hash.lower()], "fields": ["files"]})
        torrents = result.get("arguments", {}).get("torrents", [])
        files = torrents[0].get("files", []) if torrents else []
        return [{"name": f.get("name", ""), "size": f.get("length", 0)} for f in files]

    async def skip_files(self, torrent_hash: str, indices: list[int]) -> bool:
        result = await self._rpc("torrent-set", {"ids": [torrent_hash.lower()], "files-unwanted": indices})
        return result.get("result") == "success"

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        torrents = await self.get_torrents()
        for t in torrents:
            if t.get("hashString", "").lower() == torrent_hash.lower():
                await self._rpc("torrent-remove", {
                    "ids": [t["id"]], "delete-local-data": delete_files
                })
                return True
        return False

    async def stop_torrent(self, torrent_hash: str) -> bool:
        torrents = await self.get_torrents()
        for t in torrents:
            if t.get("hashString", "").lower() == torrent_hash.lower():
                await self._rpc("torrent-stop", {"ids": [t["id"]]})
                return True
        return False

    async def start_torrent(self, torrent_hash: str) -> bool:
        torrents = await self.get_torrents()
        for t in torrents:
            if t.get("hashString", "").lower() == torrent_hash.lower():
                await self._rpc("torrent-start", {"ids": [t["id"]]})
                return True
        return False

    def map_status(self, tr_status: int) -> DownloadStatus:
        # Transmission status codes: 0=stopped, 1=check-wait, 2=checking, 3=dl-wait, 4=downloading, 5=seed-wait, 6=seeding
        mapping = {
            0: DownloadStatus.PAUSED,
            1: DownloadStatus.QUEUED,
            2: DownloadStatus.DOWNLOADING,
            3: DownloadStatus.QUEUED,
            4: DownloadStatus.DOWNLOADING,
            5: DownloadStatus.QUEUED,
            6: DownloadStatus.SEEDING,
        }
        return mapping.get(tr_status, DownloadStatus.DOWNLOADING)


# ─── Unified interface ────────────────────────────────────────────────────────

class TorrentManager:
    """Single interface over whatever torrent client is configured."""

    def __init__(self, config: TorrentClientConfig):
        self.config = config
        self._qbt: Optional[QBittorrentClient] = None
        self._tr: Optional[TransmissionClient] = None

        if config.type == TorrentClientType.QBITTORRENT:
            self._qbt = QBittorrentClient(config.host, config.port, config.username, config.password)
        elif config.type == TorrentClientType.TRANSMISSION:
            self._tr = TransmissionClient(config.host, config.port, config.username, config.password)

    async def connect(self) -> bool:
        if self._qbt:
            return await self._qbt.login()
        if self._tr:
            try:
                await self._tr.get_version()
                return True
            except Exception:
                return False
        return False

    async def get_info(self) -> TorrentClientInfo:
        try:
            if self._qbt:
                await self._qbt.login()
                version = await self._qbt.get_version()
                torrents = await self._qbt.get_torrents()
                active = sum(1 for t in torrents if t.get("state") in ("downloading", "stalledDL"))
                return TorrentClientInfo(
                    type=TorrentClientType.QBITTORRENT,
                    host=self.config.host,
                    port=self.config.port,
                    reachable=True,
                    version=version,
                    active_downloads=active,
                )
            elif self._tr:
                version = await self._tr.get_version()
                torrents = await self._tr.get_torrents()
                active = sum(1 for t in torrents if t.get("status") == 4)
                return TorrentClientInfo(
                    type=TorrentClientType.TRANSMISSION,
                    host=self.config.host,
                    port=self.config.port,
                    reachable=True,
                    version=version,
                    active_downloads=active,
                )
        except Exception:
            pass
        return TorrentClientInfo(
            type=self.config.type,
            host=self.config.host,
            port=self.config.port,
            reachable=False,
        )

    async def add_magnet(self, magnet_url: str, save_path: str) -> str:
        """Add a magnet URL. Returns torrent hash on success."""
        if self._qbt:
            await self._qbt.login()
            # Extract hash from magnet
            import re
            m = re.search(r"xt=urn:btih:([0-9a-fA-F]+)", magnet_url)
            success = await self._qbt.add_magnet(magnet_url, save_path)
            if success and m:
                return m.group(1).lower()
            raise TorrentClientError("qBittorrent rejected the magnet URL")

        elif self._tr:
            hash_str = await self._tr.add_magnet(magnet_url, save_path)
            if hash_str:
                return hash_str.lower()
            raise TorrentClientError("Transmission rejected the magnet URL")

        raise TorrentClientError("No torrent client configured")

    async def get_torrent_status(self, torrent_hash: str) -> Optional[dict]:
        """Get unified status dict for a torrent."""
        try:
            if self._qbt:
                await self._qbt.login()
                t = await self._qbt.get_torrent(torrent_hash)
                if not t:
                    return None
                return {
                    "hash": torrent_hash,
                    "name": t.get("name", ""),
                    "status": self._qbt.map_status(t.get("state", "")),
                    "progress": t.get("progress", 0.0),
                    "size_bytes": t.get("size", 0),
                    "downloaded_bytes": t.get("completed", 0),
                    "download_speed": t.get("dlspeed", 0),
                    "eta_seconds": t.get("eta", -1),
                    "save_path": t.get("save_path", ""),
                    "upload_ratio": t.get("ratio", 0.0),
                    "seeding_time": t.get("seeding_time", 0),
                }

            elif self._tr:
                t = await self._tr.get_torrent(torrent_hash)
                if not t:
                    return None
                return {
                    "hash": torrent_hash,
                    "name": t.get("name", ""),
                    "status": self._tr.map_status(t.get("status", 0)),
                    "progress": t.get("percentDone", 0.0),
                    "size_bytes": t.get("sizeWhenDone") or t.get("totalSize", 0),
                    "downloaded_bytes": t.get("downloadedEver", 0),
                    "download_speed": t.get("rateDownload", 0),
                    "eta_seconds": t.get("eta", -1),
                    "save_path": t.get("downloadDir", ""),
                    "upload_ratio": t.get("uploadRatio", 0.0),
                    "seeding_time": t.get("secondsSeeding", 0),
                }
        except Exception:
            pass
        return None

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        try:
            if self._qbt:
                await self._qbt.login()
                return await self._qbt.delete_torrent(torrent_hash, delete_files)
            elif self._tr:
                return await self._tr.delete_torrent(torrent_hash, delete_files)
        except Exception:
            pass
        return False

    async def get_files(self, torrent_hash: str) -> list[dict]:
        """The torrent's files in client order; empty until its metadata arrives."""
        try:
            if self._qbt:
                await self._qbt.login()
                return await self._qbt.get_files(torrent_hash)
            elif self._tr:
                return await self._tr.get_files(torrent_hash)
        except Exception:
            pass
        return []

    async def skip_files(self, torrent_hash: str, indices: list[int]) -> bool:
        """Leave these files (by index) undownloaded."""
        if not indices:
            return True
        try:
            if self._qbt:
                await self._qbt.login()
                return await self._qbt.skip_files(torrent_hash, indices)
            elif self._tr:
                return await self._tr.skip_files(torrent_hash, indices)
        except Exception:
            pass
        return False

    async def stop_torrent(self, torrent_hash: str) -> bool:
        """Stop seeding/downloading a torrent without removing it."""
        try:
            if self._qbt:
                await self._qbt.login()
                return await self._qbt.stop_torrent(torrent_hash)
            elif self._tr:
                return await self._tr.stop_torrent(torrent_hash)
        except Exception:
            pass
        return False

    async def start_torrent(self, torrent_hash: str) -> bool:
        """Resume a previously stopped torrent."""
        try:
            if self._qbt:
                await self._qbt.login()
                return await self._qbt.start_torrent(torrent_hash)
            elif self._tr:
                return await self._tr.start_torrent(torrent_hash)
        except Exception:
            pass
        return False


# ─── Auto-discovery ───────────────────────────────────────────────────────────

async def discover_torrent_clients() -> list[TorrentClientInfo]:
    """Probe common ports and return all reachable torrent clients."""
    candidates = [
        (TorrentClientType.QBITTORRENT, "localhost", 8080, "admin", "adminadmin"),
        (TorrentClientType.QBITTORRENT, "localhost", 8081, "admin", "adminadmin"),
        (TorrentClientType.TRANSMISSION, "localhost", 9091, "", ""),
        (TorrentClientType.TRANSMISSION, "localhost", 9092, "", ""),
    ]
    found = []

    async def probe(client_type, host, port, username, password):
        cfg = TorrentClientConfig(type=client_type, host=host, port=port, username=username, password=password)
        manager = TorrentManager(cfg)
        info = await manager.get_info()
        if info.reachable:
            found.append(info)

    await asyncio.gather(*[probe(*c) for c in candidates], return_exceptions=True)
    return found
