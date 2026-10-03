"""Sparrow's own Transmission: a supervised, local-only download app.

When no download app is reachable, Sparrow can run transmission-daemon itself.
It listens on 127.0.0.1 only, requires a generated password and keeps its state
under the data directory. Sparrow passes each transfer's folder explicitly, so
the daemon's own download folder is only a fallback.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import platform
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("sparrow.downloader")

EXECUTABLE = "transmission-daemon"
RPC_PORTS = range(9091, 9191)
PEER_PORTS = range(51413, 51513)
READY_TIMEOUT = 15.0


def find_executable() -> Optional[str]:
    found = shutil.which(EXECUTABLE)
    if found:
        return found
    # launchd and service managers often start Sparrow without Homebrew on PATH.
    for candidate in (
        "/opt/homebrew/bin/transmission-daemon",
        "/usr/local/bin/transmission-daemon",
        "/usr/bin/transmission-daemon",
    ):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def install_hint() -> str:
    """One line the owner can run to make Transmission available; "" if it is."""
    if find_executable():
        return ""
    if in_container():
        return "docker compose build"
    if sys.platform == "darwin":
        return "brew install transmission-cli"
    if sys.platform.startswith("win"):
        return "winget install Transmission.Transmission"
    ids = ""
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            key, _, value = line.partition("=")
            if key in ("ID", "ID_LIKE"):
                ids += " " + value.strip().strip('"').lower()
    except OSError:
        pass
    if any(name in ids for name in ("debian", "ubuntu")):
        return "sudo apt install transmission-daemon"
    if any(name in ids for name in ("fedora", "rhel", "centos")):
        return "sudo dnf install transmission-daemon"
    if "arch" in ids:
        return "sudo pacman -S transmission-cli"
    if "alpine" in ids:
        return "sudo apk add transmission-daemon"
    if "suse" in ids:
        return "sudo zypper install transmission-daemon"
    return f"Install transmission-daemon for {platform.system() or 'this system'}"


def port_free(port: int, udp: bool = False) -> bool:
    kinds = (socket.SOCK_STREAM, socket.SOCK_DGRAM) if udp else (socket.SOCK_STREAM,)
    for kind in kinds:
        with socket.socket(socket.AF_INET, kind) as sock:
            # Transmission itself sets SO_REUSEADDR, so a port still in TIME_WAIT
            # after a restart is usable; an active listener is not.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1" if kind == socket.SOCK_STREAM else "0.0.0.0", port))
            except OSError:
                return False
    return True


def choose_port(preferred: Optional[int], candidates: range, udp: bool = False) -> int:
    if preferred and port_free(preferred, udp):
        return preferred
    for port in candidates:
        if port != preferred and port_free(port, udp):
            return port
    raise RuntimeError("No free local port for Transmission.")


def write_settings(path: Path, required: dict) -> dict:
    """Merge Sparrow's required keys into settings.json, keeping everything else."""
    settings: dict = {}
    try:
        loaded = json.loads(path.read_text())
        if isinstance(loaded, dict):
            settings = loaded
    except (OSError, ValueError):
        pass
    settings.update(required)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(".tmp")
    # The plaintext password is hashed by Transmission on its next save.
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(settings, handle, indent=4, sort_keys=True)
    os.replace(temp, path)
    return settings


class ManagedTransmission:
    """One supervised transmission-daemon per Sparrow data directory."""

    def __init__(self, data_dir, executable: Optional[str] = None):
        self.config_dir = Path(data_dir) / "downloader" / "transmission"
        self._executable = executable
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._supervisor: Optional[asyncio.Task] = None
        self._reader: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._stopping = False
        self._params: Optional[dict] = None
        self._started_at = 0.0
        self.tail: collections.deque[str] = collections.deque(maxlen=20)

    @property
    def executable(self) -> Optional[str]:
        return self._executable or find_executable()

    @property
    def available(self) -> bool:
        return bool(self.executable)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def rpc_port(self) -> Optional[int]:
        return self._params["rpc_port"] if self._params else None

    def required_settings(self, params: dict) -> dict:
        return {
            "download-dir": params["download_dir"],
            "incomplete-dir-enabled": False,
            "rpc-enabled": True,
            "rpc-bind-address": "127.0.0.1",
            "rpc-port": params["rpc_port"],
            "rpc-authentication-required": True,
            "rpc-username": params["username"],
            "rpc-password": params["password"],
            "rpc-whitelist-enabled": True,
            "rpc-whitelist": "127.0.0.1,::1",
            # Bound to loopback already; names like "localhost" must still work.
            "rpc-host-whitelist-enabled": False,
            "peer-port": params["peer_port"],
            "peer-port-random-on-start": False,
            "umask": 18,
            "start-added-torrents": True,
            "watch-dir-enabled": False,
            # Sparrow enforces seeding limits itself.
            "ratio-limit-enabled": False,
            "idle-seeding-limit-enabled": False,
            "lpd-enabled": False,
            "port-forwarding-enabled": not in_container(),
        }

    async def start(
        self,
        *,
        download_dir: str,
        username: str,
        password: str,
        rpc_port: Optional[int] = None,
    ) -> int:
        """Start (or keep) the daemon; returns the RPC port it listens on."""
        async with self._lock:
            if (
                self.running
                and self._params
                and self._params["username"] == username
                and self._params["password"] == password
            ):
                return self._params["rpc_port"]
            await self._stop_locked()
            if not self.executable:
                raise RuntimeError("Transmission is not installed.")
            settings_path = self.config_dir / "settings.json"
            previous_peer = None
            try:
                previous_peer = int(json.loads(settings_path.read_text()).get("peer-port"))
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            fallback = self.config_dir / "incoming"
            if not download_dir or not Path(download_dir).is_dir():
                fallback.mkdir(parents=True, exist_ok=True)
                download_dir = str(fallback)
            self._params = {
                "download_dir": download_dir,
                "username": username,
                "password": password,
                "rpc_port": choose_port(rpc_port, RPC_PORTS),
                "peer_port": choose_port(previous_peer, PEER_PORTS, udp=True),
            }
            self._stopping = False
            await self._spawn()
            self._supervisor = asyncio.create_task(self._supervise())
            return self._params["rpc_port"]

    async def _spawn(self) -> None:
        write_settings(self.config_dir / "settings.json", self.required_settings(self._params))
        self.tail.clear()
        self._proc = await asyncio.create_subprocess_exec(
            self.executable,
            "--foreground",
            "--config-dir",
            str(self.config_dir),
            "--log-error",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._started_at = time.monotonic()
        self._reader = asyncio.create_task(self._read(self._proc))
        log.info(
            "Started Sparrow's Transmission",
            extra={"rpc_port": self._params["rpc_port"], "pid": self._proc.pid},
        )

    async def _read(self, proc) -> None:
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                text = line.decode(errors="replace").strip()
                if text:
                    self.tail.append(text)
        except (asyncio.CancelledError, Exception):
            return

    async def _supervise(self) -> None:
        delay = 1.0
        while not self._stopping:
            proc = self._proc
            code = await proc.wait()
            if self._stopping:
                return
            if time.monotonic() - self._started_at > 60:
                delay = 1.0
            log.warning(
                "Sparrow's Transmission stopped; restarting",
                extra={"code": code, "detail": list(self.tail)[-3:], "retry_in": delay},
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)
            if self._stopping:
                return
            try:
                # Restart on the same ports so the saved connection stays valid.
                await self._spawn()
            except Exception:
                log.exception("Could not restart Sparrow's Transmission")
                self._started_at = time.monotonic()
                await asyncio.sleep(delay)

    async def wait_ready(self, timeout: float = READY_TIMEOUT) -> bool:
        from .torrent_client import TransmissionClient

        if not self._params:
            return False
        client = TransmissionClient(
            "127.0.0.1", self._params["rpc_port"], self._params["username"], self._params["password"]
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running:
                return False
            try:
                if (await client.get_version()) not in ("", "unknown"):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
        return False

    async def recover(self) -> bool:
        """Restart a stopped or unresponsive daemon with its last settings."""
        async with self._lock:
            if not self._params:
                return False
            await self._stop_locked()
            self._stopping = False
            try:
                await self._spawn()
            except Exception:
                log.exception("Could not restart Sparrow's Transmission")
                return False
            self._supervisor = asyncio.create_task(self._supervise())
        return await self.wait_ready()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        self._stopping = True
        supervisor, self._supervisor = self._supervisor, None
        if supervisor and supervisor is not asyncio.current_task():
            supervisor.cancel()
            try:
                await supervisor
            except (asyncio.CancelledError, Exception):
                pass
        proc = self._proc
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                # Transmission saves resume data on a clean stop.
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            log.info("Stopped Sparrow's Transmission")
        reader, self._reader = self._reader, None
        if reader:
            reader.cancel()


_instances: dict[str, ManagedTransmission] = {}
_current: Optional[ManagedTransmission] = None


def instance(data_dir) -> ManagedTransmission:
    global _current
    key = str(Path(data_dir).resolve(strict=False))
    if key not in _instances:
        _instances[key] = ManagedTransmission(key)
    _current = _instances[key]
    return _current


def current() -> Optional[ManagedTransmission]:
    return _current


async def start_from_config(storage) -> None:
    """Boot: bring back Sparrow's Transmission if the saved connection is managed."""
    config = storage.get_config()
    client = config.torrent_client
    if not client.managed:
        return
    daemon = instance(storage.data_dir)
    if not daemon.available:
        log.warning("Sparrow's Transmission is set up but transmission-daemon is missing")
        return
    try:
        port = await daemon.start(
            download_dir=config.staging_dir,
            username=client.username or "sparrow",
            password=client.password,
            rpc_port=client.port,
        )
    except Exception:
        log.exception("Could not start Sparrow's Transmission")
        return
    if port != client.port:
        from ..models import SparrowConfig

        latest = SparrowConfig.from_dict(storage.get_config().to_dict())
        if latest.torrent_client.managed:
            latest.torrent_client.port = port
            await storage.save_config(latest)
    if not await daemon.wait_ready():
        log.warning(
            "Sparrow's Transmission did not answer after starting",
            extra={"detail": list(daemon.tail)[-3:]},
        )


async def shutdown() -> None:
    for daemon in list(_instances.values()):
        try:
            await daemon.stop()
        except Exception:
            log.exception("Could not stop Sparrow's Transmission")
