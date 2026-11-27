"""Sparrow Node updating itself, the way spawnd does.

On every check-in the server says which version is current and whether this
computer should take it (Update by itself, or someone pressed Update). The node
then fetches the new package from the server, checks it starts, waits for work
in progress to finish, swaps it in, keeps the version before, and restarts
itself in place. A new version that doesn't reach the server within its
probation goes back to the one before, and says why.

Layout, beside node.json: app/ (running), app.next/ (being fetched),
app.previous/ (the one before), update.json (where an update is).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

PROBATION = 300.0  # Seconds a new version has to reach the server.
RETRY_FAILED_AFTER = 6 * 3600.0
WAIT_FOR_WORK = 600.0  # How long an update waits for operations to finish.
STEPS = ("downloading", "checking", "waiting", "restarting")


def app_folder() -> Path:
    return Path(__file__).resolve().parents[2]


class Updater:
    def __init__(self, config_path, client=None, *, own_version=None, app=None, restart=None):
        self.config_path = Path(config_path)
        self.app = Path(app) if app else app_folder()
        self.home = self.app.parent
        self.client = client  # httpx.AsyncClient with the node's credential.
        self.own_version = own_version if own_version is not None else os.getenv("SPARROW_NODE_VERSION", "")
        self.state_path = self.home / "update.json"
        self._restart = restart or self._exec
        self._task = None
        self.busy = lambda: False  # Work in progress that an update should wait for.
        self.before_restart = None  # Async: stop the download app and other children.

    # ─── What it reports ─────────────────────────────────────────────

    @property
    def supported(self) -> bool:
        """Only a node installed by the setup command can replace its own folder."""
        return (
            not getattr(sys, "frozen", False)
            and self.app.name == "app"
            and (self.app / "sparrow-node.py").is_file()
            and os.access(self.home, os.W_OK)
            and bool(self.own_version)
        )

    def state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, **state):
        temporary = self.state_path.with_suffix(".pending")
        temporary.write_text(json.dumps({**state, "at": state.get("at", time.time())}))
        os.replace(temporary, self.state_path)

    def capabilities(self) -> dict:
        state = self.state()
        report = {"state": state.get("state") or "idle"}
        if report["state"] in ("current", "probation"):
            report["state"] = "idle"
        for key in ("target", "error", "at"):
            if state.get(key):
                report[key] = state[key]
        return {"self_update": self.supported, "update": report}

    # ─── Starting after an update ────────────────────────────────────

    async def started(self):
        """Settle an update this process is the result of; go back if it ran out of time."""
        state = self.state()
        if state.get("state") not in ("restarting", "probation"):
            return
        if state.get("to") != self.own_version:
            # Something else is running than the update meant: it didn't take.
            self._save(state="failed", target=state.get("to", ""), error="The new version didn't start, so the one before is back.")
            return
        began = float(state.get("began") or state.get("at") or time.time())
        if time.time() - began > PROBATION:
            await self.rollback("The new version didn't reach Sparrow, so the one before is back.")
            return
        self._save(state="probation", to=self.own_version, began=began, **{"from": state.get("from", "")})
        asyncio.get_running_loop().call_later(
            max(1.0, began + PROBATION - time.time()),
            lambda: asyncio.ensure_future(self._probation_over()),
        )

    def reached_server(self):
        """The server answered: a new version on probation is now the one."""
        if self.state().get("state") == "probation":
            self._save(state="current", version=self.own_version)

    async def _probation_over(self):
        if self.state().get("state") == "probation":
            await self.rollback("The new version didn't reach Sparrow, so the one before is back.")

    async def rollback(self, reason):
        previous = self.home / "app.previous"
        bad = self.own_version
        if not previous.is_dir():
            self._save(state="failed", target=bad, error=reason)
            return
        failed = self.home / "app.failed"
        shutil.rmtree(failed, ignore_errors=True)
        os.replace(self.app, failed)
        os.replace(previous, self.app)
        self._save(state="failed", target=bad, error=reason)
        await self._stop_children()
        self._restart()

    # ─── Taking a new version ────────────────────────────────────────

    def consider(self, offer):
        """Called with the server's update offer on every check-in."""
        if not isinstance(offer, dict) or not self.supported:
            return
        target = str(offer.get("version") or "")
        if not target or target == self.own_version:
            return
        if self._task and not self._task.done():
            return
        state = self.state()
        requested = float(offer.get("requested") or 0)
        tried = float(state.get("at") or 0)
        if state.get("state") == "failed" and state.get("target") == target:
            # A failed version is tried again when asked, or after a while.
            if not (requested > tried or (offer.get("auto") and time.time() - tried > RETRY_FAILED_AFTER)):
                return
        elif not (offer.get("auto") or requested > tried):
            return
        self._task = asyncio.ensure_future(self.update(target))

    async def update(self, target):
        nxt = self.home / "app.next"
        try:
            self._save(state="downloading", target=target)
            shutil.rmtree(nxt, ignore_errors=True)
            response = await self.client.get("/api/v1/node/package.tar.gz", timeout=300)
            response.raise_for_status()
            await asyncio.to_thread(self._unpack, response.content, nxt)
            self._save(state="checking", target=target)
            version = await asyncio.to_thread(self._check, nxt)
            self._save(state="waiting", target=version)
            deadline = time.monotonic() + WAIT_FOR_WORK
            while self.busy() and time.monotonic() < deadline:
                await asyncio.sleep(5)
            self._save(state="restarting", target=version, to=version, began=time.time(), **{"from": self.own_version})
            previous = self.home / "app.previous"
            shutil.rmtree(previous, ignore_errors=True)
            os.replace(self.app, previous)
            os.replace(nxt, self.app)
        except Exception as exc:  # The row says why; the running version carries on.
            shutil.rmtree(nxt, ignore_errors=True)
            self._save(state="failed", target=target, error=_plain(exc))
            return
        await self._stop_children()
        self._restart()

    def _unpack(self, data, folder):
        folder.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive.getmembers():
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                    raise ValueError("The new version's package wasn't as expected.")
            archive.extractall(folder, filter="data") if sys.version_info >= (3, 12) else archive.extractall(folder)

    def _check(self, folder) -> str:
        """Prove the new version starts before switching to it."""
        stamped = (folder / "VERSION").read_text().strip()
        result = subprocess.run(
            [sys.executable, str(folder / "sparrow-node.py"), "--config", str(self.config_path), "version"],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=120,
            env=fresh_environment(),
        )
        try:
            answer = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError("The new version didn't start.") from None
        if result.returncode != 0 or answer.get("version") != stamped:
            raise RuntimeError("The new version didn't start.")
        return stamped

    async def _stop_children(self):
        if self.before_restart:
            try:
                await self.before_restart()
            except Exception:  # Restarting matters more than a tidy stop.
                pass

    def _exec(self):
        launcher = self.app / "sparrow-node.py"
        os.execve(
            sys.executable,
            [sys.executable, str(launcher), "--config", str(self.config_path), "run"],
            fresh_environment(),
        )


def fresh_environment() -> dict:
    """This environment without the running version's labels, so the new one stamps its own."""
    return {k: v for k, v in os.environ.items() if k not in ("SPARROW_NODE_VERSION", "SPARROW_NODE_RELEASE")}


def _plain(exc) -> str:
    if hasattr(exc, "response") and getattr(exc, "response", None) is not None:
        return f"Sparrow didn't send the new version ({exc.response.status_code})."
    text = str(exc)
    if isinstance(exc, (OSError, subprocess.SubprocessError)) and not isinstance(exc, FileNotFoundError):
        return f"Couldn't install the new version: {type(exc).__name__}."
    return text if text and len(text) < 160 else "The update didn't finish."
