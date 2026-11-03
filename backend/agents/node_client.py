"""Sparrow Node: outbound connection, durable results and portable execution.

Run `python -m backend.agents.node_client configure` once, then `... run`.
Release installers invoke these commands with bundled Python and media tools.
"""

from __future__ import annotations

import argparse
import hashlib
import asyncio
import json
import os
import secrets
import signal
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from .node_executor import Executor, NodeError, PROTOCOL, library_roots



def atomic_config(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def root_marker(path):
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    marker = path / ".sparrow-root-id"
    if not marker.exists():
        marker.write_text(secrets.token_hex(16))
    return str(path), marker.read_text().strip()


async def configure(args):
    server = args.server.rstrip("/")
    parsed = urlparse(server)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http"
        and (
            parsed.hostname in ("localhost", "127.0.0.1", "::1")
            or args.allow_private_http
        )
    ):
        raise ValueError(
            "Use HTTPS, or explicitly select --allow-private-http for a trusted private network."
        )
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError(
            "Enter the server origin without credentials, paths or query parameters."
        )
    folders = [args.library] if isinstance(args.library, str) else list(args.library)
    libraries = [root_marker(folder) for folder in folders]
    staging, staging_marker = root_marker(args.staging)
    chosen = [Path(folder) for folder, _ in libraries] + [Path(staging)]
    for number, a in enumerate(chosen):
        for b in chosen[number + 1 :]:
            if a == b or a in b.parents or b in a.parents:
                raise ValueError("Library and staging must be separate folders.")
    roots = library_roots([folder for folder, _ in libraries])
    markers = dict(zip(roots, [marker for _, marker in libraries]))
    path = Path(args.config)
    previous = json.loads(path.read_text()) if path.exists() else {}
    config = {
        "server": server,
        "credential": (
            previous.get("credential")
            if previous.get("enrollment_fingerprint")
            == hashlib.sha256(args.code.encode()).hexdigest()
            and previous.get("server") == server
            else secrets.token_urlsafe(32)
        ),
        "enrollment_fingerprint": hashlib.sha256(args.code.encode()).hexdigest(),
        "roots": {**roots, "staging": staging},
        "markers": {**markers, "staging": staging_marker},
        "downloader": (
            json.loads(Path(args.downloader_config).read_text())
            if args.downloader_config
            else {
                # Sparrow Node runs its own Transmission, like the server can.
                "type": "transmission",
                "managed": True,
                "host": "127.0.0.1",
                "port": 0,
                "username": "sparrow",
                "password": secrets.token_urlsafe(24),
            }
            if getattr(args, "managed_downloader", False)
            else previous.get("downloader")
        ),
        "node_id": args.node_id or previous.get("node_id"),
    }
    atomic_config(path, config)
    executor = Executor(
        path.parent, config["roots"], config["markers"], config["downloader"]
    )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            server + "/api/v1/node/pair",
            json={
                "code": args.code,
                "credential": config["credential"],
                "capabilities": executor.capabilities(),
            },
        )
        response.raise_for_status()
        config["node_id"] = response.json()["node_id"]
    atomic_config(path, config)
    print("Storage paired. Run Sparrow Node to keep this collection available.")


def fetcher(config):
    """Copies arriving from other storage, read through the server."""

    async def fetch(token, offset):
        try:
            async with httpx.AsyncClient(
                base_url=config["server"],
                timeout=httpx.Timeout(120),
                headers={"Authorization": "Bearer " + config["credential"]},
            ) as client:
                async with client.stream(
                    "GET", f"/api/v1/node/transfers/{token}", params={"offset": offset}
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        yield chunk
        except httpx.HTTPError as exc:
            # The part already copied is kept; the next attempt resumes there.
            raise NodeError(f"The copy was interrupted ({type(exc).__name__}).") from exc

    return fetch


async def managed_downloader(config_path, config):
    """Start this node's own Transmission, saving the port it listens on."""
    downloader = config.get("downloader") or {}
    if not downloader.get("managed"):
        return None
    from ..services.managed_transmission import ManagedTransmission

    daemon = ManagedTransmission(config_path.parent)
    if not daemon.available:
        print("transmission-daemon is missing; this node can't download.", flush=True)
        return None
    port = await daemon.start(
        download_dir=config["roots"]["staging"],
        username=downloader["username"],
        password=downloader["password"],
        rpc_port=downloader.get("port") or None,
    )
    if port != downloader.get("port"):
        downloader["port"] = port
        atomic_config(config_path, config)
    await daemon.wait_ready()
    return daemon


async def run(config_path):
    config_path = Path(config_path)
    config = json.loads(config_path.read_text())
    daemon = await managed_downloader(config_path, config)
    executor = Executor(
        config_path.parent,
        config["roots"],
        config.get("markers"),
        config.get("downloader"),
        fetcher(config),
    )
    async with httpx.AsyncClient(
        base_url=config["server"],
        timeout=httpx.Timeout(35),
        headers={"Authorization": "Bearer " + config["credential"]},
    ) as client:
        running = set()

        async def worker(number):
            delay = 1
            while True:
                try:
                    for receipt in executor.pending_results():
                        response = await client.post(
                            "/api/v1/node/results/" + receipt["id"],
                            json={"result": receipt["result"]},
                        )
                        if response.status_code == 401:
                            raise PermissionError(
                                "Node access was revoked. Pair again from Storage settings."
                            )
                        if response.status_code == 409:
                            executor.delivered(
                                receipt["id"]
                            )  # Server no longer has this ephemeral operation.
                        else:
                            response.raise_for_status()
                            executor.delivered(receipt["id"])
                    response = await client.post(
                        "/api/v1/node/poll",
                        json={
                            "capabilities": executor.capabilities(),
                            "active_operations": list(running),
                        },
                    )
                    if response.status_code == 401:
                        raise PermissionError(
                            "Node access was revoked. Pair again from Storage settings."
                        )
                    response.raise_for_status()
                    command = response.json().get("command")
                    if command:
                        running.add(command["id"])
                        try:
                            await executor.execute(command)
                        finally:
                            running.discard(command["id"])
                    delay = 1
                except (httpx.HTTPError, OSError) as exc:
                    if isinstance(exc, PermissionError):
                        raise
                    # Do not log request URLs/headers, which could expose credentials.
                    if number == 0:
                        print(
                            f"Waiting for the Sparrow server; retrying in {delay}s.",
                            flush=True,
                        )
                    await asyncio.sleep(delay)
                    delay = min(20, delay * 2)

        try:
            try:
                async with asyncio.TaskGroup() as group:
                    for number in range(4):
                        group.create_task(worker(number))
            except* PermissionError:
                await executor.stop_owned_downloads()
                raise
        finally:
            await executor.shutdown()
            if daemon:
                await daemon.stop()


def main():
    default = (
        Path(os.getenv("LOCALAPPDATA", Path.home() / ".local/share"))
        / "SparrowNode"
        / "node.json"
    )
    parser = argparse.ArgumentParser(description="Sparrow storage node")
    parser.add_argument("--config", default=str(default))
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure")
    for name in ("server", "code", "staging"):
        setup.add_argument("--" + name, required=True)
    # Repeat --library for each disk this computer offers, main one first.
    setup.add_argument("--library", required=True, action="append")
    setup.add_argument("--node-id")
    setup.add_argument("--downloader-config")
    setup.add_argument(
        "--managed-downloader",
        action="store_true",
        help="Run Sparrow's own Transmission for downloads on this computer.",
    )
    setup.add_argument("--allow-private-http", action="store_true")
    sub.add_parser("run")
    sub.add_parser("check")
    args = parser.parse_args()
    try:
        if args.command == "configure":
            asyncio.run(configure(args))
        elif args.command == "run":
            asyncio.run(run(args.config))
        else:
            config = json.loads(Path(args.config).read_text())
            executor = Executor(
                Path(args.config).parent,
                config["roots"],
                config.get("markers"),
                config.get("downloader"),
            )
            print(json.dumps(executor.capabilities(), indent=2))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(
            f"Sparrow Node could not continue: {type(exc).__name__}. Check its configuration and server access.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--subtitle-worker":
        from .subtitle_worker import main as worker_main

        raise SystemExit(worker_main(sys.argv[2]))
    if len(sys.argv) > 2 and sys.argv[1] == "--subtitle-evidence":
        from .subtitle_evidence import main as evidence_main

        raise SystemExit(
            evidence_main(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else None)
        )
    main()
