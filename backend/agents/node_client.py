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
from .node_executor import Executor, NodeError, PROTOCOL, library_roots, root_marker


def atomic_config(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


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
    path = Path(args.config)
    previous = json.loads(path.read_text()) if path.exists() else {}
    folders = [args.library] if isinstance(args.library, str) else list(args.library or [])
    if bool(folders) != bool(args.staging):
        raise ValueError("Give both --library and --staging, or neither and choose them in Sparrow.")
    if folders:
        from .folders import labelled, overlap

        chosen = [str(Path(f).expanduser().resolve()) for f in [*folders, args.staging]]
        problem = overlap(labelled(chosen[:-1], chosen[-1]))
        if problem:
            raise ValueError(problem)
        marked = [root_marker(f) for f in chosen]
        roots = {**library_roots([f for f, _ in marked[:-1]]), "staging": marked[-1][0]}
        markers = dict(zip(roots, [m for _, m in marked]))
    else:
        # Pairing again keeps this computer's folders; a new one chooses them in Sparrow.
        roots, markers = previous.get("roots") or {}, previous.get("markers") or {}
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
        "roots": roots,
        "markers": markers,
        # Places folders may be chosen from; only this computer can change them.
        "places": list(getattr(args, "folder_root", None) or []) or previous.get("places") or None,
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
        path.parent, config["roots"], config["markers"], config["downloader"],
        places=config["places"], on_folders=lambda *_: None,
    )
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(
                server + "/api/v1/node/pair",
                json={
                    "code": args.code,
                    "credential": config["credential"],
                    "capabilities": executor.capabilities(),
                },
            )
        except httpx.HTTPError:
            raise ValueError(f"Sparrow didn’t answer at {server}. Check the address and that this computer can reach it.") from None
        if response.status_code in (400, 404, 409, 422):
            detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else ""
            raise ValueError(detail if isinstance(detail, str) and detail else "Sparrow refused this pairing code. Get a fresh command in Sparrow.")
        response.raise_for_status()
        config["node_id"] = response.json()["node_id"]
    atomic_config(path, config)
    print("Paired with Sparrow.")


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


def speech_fetchers(config):
    """Read this server's shared speech models with the node's own credential."""
    headers = {"Authorization": "Bearer " + config["credential"]}

    async def models():
        async with httpx.AsyncClient(base_url=config["server"], timeout=60, headers=headers) as client:
            response = await client.get("/api/v1/node/models")
            response.raise_for_status()
            return response.json()

    async def model_file(name, file, offset):
        async with httpx.AsyncClient(
            base_url=config["server"], timeout=httpx.Timeout(120), headers=headers
        ) as client:
            async with client.stream(
                "GET", f"/api/v1/node/models/{name}/{file}", params={"offset": offset}
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(1024 * 1024):
                    yield chunk

    return models, model_file


async def managed_downloader(config_path, config):
    """Start this node's own Transmission, saving the port it listens on.

    Returns (daemon, state): state is how the download app is doing, for the
    computer's Downloads row (None when it is someone else's download app).
    """
    downloader = config.get("downloader") or {}
    if not downloader or downloader.get("type") == "none":
        return None, "none"
    if not downloader.get("managed"):
        return None, None
    staging = (config.get("roots") or {}).get("staging")
    if not staging:
        return None, None  # Starts once this computer's folders are chosen.
    from ..services.managed_transmission import ManagedTransmission

    daemon = ManagedTransmission(config_path.parent)
    if not daemon.available:
        print("transmission-daemon is missing; this node can't download.", flush=True)
        return None, "missing"
    try:
        port = await daemon.start(
            download_dir=staging,
            username=downloader["username"],
            password=downloader["password"],
            rpc_port=downloader.get("port") or None,
        )
    except Exception as exc:  # The row says it stopped; the next check retries.
        print(f"Sparrow's Transmission didn't start: {type(exc).__name__}.", flush=True)
        return None, "failed"
    if port != downloader.get("port"):
        downloader["port"] = port
        atomic_config(config_path, config)
    await daemon.wait_ready()
    return daemon, "running"


def wants(config, job):
    """Jobs not heard from the server yet start on, as they did before jobs existed."""
    return (config.get("jobs") or {}).get(job, job != "subtitles")


async def run(config_path):
    config_path = Path(config_path)
    config = json.loads(config_path.read_text())
    daemon = None
    downloads_lock = asyncio.Lock()

    async def follow_downloads():
        """Run the download app while Downloads is on, and stop it when it's off."""
        nonlocal daemon
        async with downloads_lock:
            if not wants(config, "downloads"):
                if daemon:
                    await daemon.stop()
                    daemon = None
                executor.downloader_state = "off"
                return
            if daemon:
                executor.downloader_state = "running" if daemon.running else "failed"
                return
            executor.downloader_state = "starting"
            daemon, executor.downloader_state = await managed_downloader(config_path, config)

    async def save_folders(roots, markers):
        nonlocal daemon
        moved = (config.get("roots") or {}).get("staging") != roots.get("staging")
        config["roots"], config["markers"] = roots, markers
        atomic_config(config_path, config)
        if moved:
            # Downloads land in the incoming folder, so Transmission follows it.
            async with downloads_lock:
                if daemon:
                    await daemon.stop()
                    daemon = None
            await follow_downloads()

    executor = Executor(
        config_path.parent,
        config["roots"],
        config.get("markers"),
        config.get("downloader"),
        fetcher(config),
        places=config.get("places"),
        on_folders=save_folders,
    )
    executor.jobs = dict(config.get("jobs") or {})
    await follow_downloads()
    jobs_hooks = []  # Called with (before, after) when the server changes this computer's jobs.
    executor.jobs_hooks = jobs_hooks

    # Subtitles: the speech engine sets itself up while the job is on.
    from .speech_setup import NodeSpeech

    speech = NodeSpeech(config_path.parent, *speech_fetchers(config))

    async def follow_subtitles(before, after):
        if after.get("subtitles"):
            speech.ensure()
        elif before.get("subtitles"):
            await speech.stop()

    jobs_hooks.append(follow_subtitles)
    if wants(config, "subtitles"):
        speech.ensure()

    async def keep_speech():
        """Retry a stopped setup now and then while Subtitles stays on."""
        while True:
            await asyncio.sleep(1800)
            if wants(config, "subtitles"):
                speech.ensure()

    async def apply_jobs(jobs):
        if not isinstance(jobs, dict) or jobs == config.get("jobs"):
            return
        before = dict(config.get("jobs") or {})
        config["jobs"] = executor.jobs = {k: bool(v) for k, v in jobs.items()}
        atomic_config(config_path, config)
        if before.get("downloads", True) != config["jobs"].get("downloads", True):
            await follow_downloads()
        for hook in list(jobs_hooks):
            try:
                await hook(before, config["jobs"])
            except Exception as exc:  # One job's setup never stops the others.
                print(f"Couldn't follow a job change: {type(exc).__name__}.", flush=True)

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
                    if daemon and executor.downloader_state in ("running", "failed"):
                        executor.downloader_state = "running" if daemon.running else "failed"
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
                    answer = response.json()
                    await apply_jobs(answer.get("jobs"))
                    command = answer.get("command")
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
                    group.create_task(keep_speech())
            except* PermissionError:
                await executor.stop_owned_downloads()
                raise
        finally:
            await speech.stop()
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
    for name in ("server", "code"):
        setup.add_argument("--" + name, required=True)
    # Folders may be given here, or chosen afterwards from Sparrow.
    # Repeat --library for each disk this computer offers, main one first.
    setup.add_argument("--library", action="append")
    setup.add_argument("--staging")
    setup.add_argument(
        "--folder-root",
        action="append",
        help="A place Sparrow may choose folders in (repeatable). Default: home, drives and mounted disks.",
    )
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
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
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
