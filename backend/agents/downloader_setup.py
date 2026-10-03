"""Download app setup: find one, connect one, or let Sparrow run Transmission."""

import asyncio
import os
import secrets
import urllib.parse
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .account_api import administrator
from ..models import SparrowConfig, TorrentClientConfig, TorrentClientType
from ..services import managed_transmission
from ..services.torrent_client import (
    TorrentManager,
    discover_download_apps,
    probe_qbittorrent,
    probe_transmission,
)

Brand = Literal["transmission", "qbittorrent"]
MANAGED_USER = "sparrow"


class Discover(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Brand | None = None


class Connect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Brand
    host: str = Field(min_length=1, max_length=260)
    port: int = Field(ge=1, le=65535)
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=500)


def brand(kind) -> str:
    return "Transmission" if TorrentClientType(kind) == TorrentClientType.TRANSMISSION else "qBittorrent"


def clean_host(value: str, port: int) -> tuple[str, int]:
    """Accept a pasted web address as well as a bare host name."""
    value = value.strip()
    if "://" not in value:
        value = "http://" + value
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname or ""
    try:
        port = parsed.port or port
    except ValueError:
        raise HTTPException(422, "That port isn’t a number between 1 and 65535.")
    if not host or any(c.isspace() for c in host):
        raise HTTPException(422, "Enter the download app’s address, such as 192.168.1.20.")
    return host, port


def install_downloader_setup(app, storage):
    router = APIRouter(prefix="/api/v1/admin/downloader")
    lock = asyncio.Lock()

    def daemon():
        return managed_transmission.instance(storage.data_dir)

    async def status():
        client = storage.get_config().torrent_client
        reachable, version = False, ""
        if client.type != TorrentClientType.NONE:
            try:
                info = await asyncio.wait_for(TorrentManager(client).get_info(), 6)
                reachable, version = info.reachable, info.version
            except asyncio.TimeoutError:
                pass
        available = daemon().available
        return {
            "type": client.type.value,
            "host": client.host if client.type != TorrentClientType.NONE else "",
            "port": client.port if client.type != TorrentClientType.NONE else 0,
            "username": client.username,
            "managed": client.managed,
            "reachable": reachable,
            "version": version,
            "managed_available": available,
            "install_hint": "" if available else managed_transmission.install_hint(),
            "in_container": managed_transmission.in_container(),
        }

    @router.get("")
    async def current(request: Request):
        administrator(request)
        return await status()

    @router.post("/discover")
    async def discover(body: Discover, request: Request):
        administrator(request)
        found = await discover_download_apps(TorrentClientType(body.type) if body.type else None)
        client = storage.get_config().torrent_client
        if client.managed:
            # Sparrow's own Transmission is not something the owner installed.
            found = [
                app
                for app in found
                if not (
                    app["type"] == "transmission"
                    and app["host"] in ("127.0.0.1", "localhost")
                    and app["port"] == client.port
                )
            ]
        return {"found": found}

    @router.post("/connect")
    async def connect(body: Connect, request: Request):
        administrator(request)
        host, port = clean_host(body.host, body.port)
        kind = TorrentClientType(body.type)
        async with lock:
            config = SparrowConfig.from_dict(storage.get_config().to_dict())
            saved = config.torrent_client
            same = (saved.type, saved.host, saved.port) == (kind, host, port)
            if same and saved.managed:
                return await status()
            password = body.password or (saved.password if same else "")
            candidate = TorrentClientConfig(
                type=kind,
                host=host,
                port=port,
                username=body.username.strip(),
                password=password,
            )
            if not await TorrentManager(candidate).connect():
                raise HTTPException(422, await failure(kind, host, port))
            config.torrent_client = candidate
            await storage.save_config(config)
            await daemon().stop()
        return await status()

    async def failure(kind, host, port) -> str:
        async with httpx.AsyncClient(timeout=3) as client:
            answers = await asyncio.gather(
                probe_transmission(client, host, port),
                probe_qbittorrent(client, host, port),
            )
        mine, other = (answers[0], answers[1]) if kind == TorrentClientType.TRANSMISSION else (answers[1], answers[0])
        if mine and mine["problem"]:
            return mine["problem"]
        if mine:
            return f"{brand(kind)} answered but didn’t accept that username and password."
        if other:
            return f"That’s {brand(other['type'])}, not {brand(kind)}."
        return f"Nothing answered at {host}:{port}."

    @router.post("/managed")
    async def managed(request: Request):
        administrator(request)
        async with lock:
            config = SparrowConfig.from_dict(storage.get_config().to_dict())
            staging = Path(config.staging_dir) if config.staging_dir else None
            if not staging or not staging.is_dir() or not os.access(staging, os.W_OK):
                raise HTTPException(422, "Choose an incoming folder in Storage first.")
            transmission = daemon()
            if not transmission.available:
                raise HTTPException(
                    422, f"Install Transmission first: {managed_transmission.install_hint()}"
                )
            saved = config.torrent_client
            password = saved.password if saved.managed and saved.password else secrets.token_urlsafe(24)
            try:
                port = await transmission.start(
                    download_dir=str(staging),
                    username=MANAGED_USER,
                    password=password,
                    rpc_port=saved.port if saved.managed else None,
                )
            except (OSError, RuntimeError) as exc:
                raise HTTPException(422, "Transmission didn’t start. Try again, or connect your own.") from exc
            if not await transmission.wait_ready():
                await transmission.stop()
                raise HTTPException(422, "Transmission didn’t start. Try again, or connect your own.")
            latest = SparrowConfig.from_dict(storage.get_config().to_dict())
            latest.torrent_client = TorrentClientConfig(
                type=TorrentClientType.TRANSMISSION,
                host="127.0.0.1",
                port=port,
                username=MANAGED_USER,
                password=password,
                managed=True,
            )
            await storage.save_config(latest)
        return await status()

    app.include_router(router)
