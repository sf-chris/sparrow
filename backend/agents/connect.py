"""Sparrow Connect, this server's side: an opt-in HTTPS address anywhere.

Off until an administrator turns it on, and nothing is sent until then.

On: register with the Connect service (no account, email or password), get
this server's own certificate for the name it is given (the key is made and
kept here; Connect only publishes the DNS proof), serve Sparrow over HTTPS on
the outside port, and check in so Connect can confirm, by calling back, that
this server really answers there before it points the name here. Check-ins
repeat so a changed internet address follows, and the certificate renews.

Off: Connect forgets this server and its records, and the HTTPS listener and
certificate are removed. Local use is unchanged either way.
"""

import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .acme import LETS_ENCRYPT, Acme, AcmeError, expiry, new_key_pem
from .account_api import administrator

log = logging.getLogger(__name__)

SERVICE = os.environ.get("SPARROW_CONNECT_URL", "https://streamsparrow.com").rstrip("/")
DIRECTORY = os.environ.get("SPARROW_ACME_DIRECTORY", LETS_ENCRYPT)
LISTEN_PORT = int(os.environ.get("SPARROW_HTTPS_PORT", "8443"))
OUTSIDE_PORT = int(os.environ.get("SPARROW_CONNECT_PORT", str(LISTEN_PORT)))
CHECK_EVERY = 600
RENEW_WITHIN = 30 * 24 * 3600
PROOF_PATH = "/api/v1/connect/proof"
ADDRESS_SERVICES = ("https://checkip.amazonaws.com", "https://api.ipify.org")


class ConnectError(Exception):
    pass


class Listener:
    """Sparrow itself, over HTTPS, beside the main server in this process."""

    def __init__(self, app):
        self.app = app
        self.server = None
        self.task = None

    async def start(self, port, cert, key):
        import uvicorn

        class Quiet(uvicorn.Server):
            # The main server owns the process's signals.
            @contextlib.contextmanager
            def capture_signals(self):
                yield

        await self.stop()
        config = uvicorn.Config(
            self.app,
            host="0.0.0.0",
            port=port,
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
            lifespan="off",
            log_level="warning",
        )
        self.server = Quiet(config)
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(50):
            if self.server.started or self.task.done():
                break
            await asyncio.sleep(0.1)
        if self.task.done() or not self.server.started:
            await self.stop()
            raise ConnectError(f"Port {port} is in use on this server, so HTTPS couldn't start.")

    async def stop(self):
        if self.server:
            self.server.should_exit = True
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.task, 10)
        self.server = self.task = None

    @property
    def running(self):
        return bool(self.server and self.server.started and not self.task.done())


class Connect:
    def __init__(self, app, data_dir, *, service=SERVICE, directory=DIRECTORY, transport=None):
        self.folder = Path(data_dir) / "connect"
        self.state_path = self.folder / "state.json"
        self.service = service
        self.directory = directory
        self.transport = transport
        self.listener = Listener(app)
        self.loop_task = None
        self.work = None
        self.lock = asyncio.Lock()

    # ── State ─────────────────────────────────────────────────────────────
    def state(self):
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {"enabled": False}

    def save(self, **changes):
        state = {**self.state(), **changes}
        self.folder.mkdir(parents=True, exist_ok=True)
        spare = self.state_path.with_suffix(".tmp")
        fd = os.open(spare, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(spare, self.state_path)
        return state

    def private(self, name, data: bytes):
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / name
        spare = path.with_suffix(".tmp")
        fd = os.open(spare, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(spare, path)
        return path

    def status(self):
        state = self.state()
        cert = self.folder / "cert.pem"
        return {
            "enabled": bool(state.get("enabled")),
            "working": self.work,
            "service": self.service,
            "name": state.get("name"),
            "link": state.get("link"),
            "address": (
                f"https://{state['name']}"
                + ("" if state.get("port", OUTSIDE_PORT) == 443 else f":{state.get('port', OUTSIDE_PORT)}")
                if state.get("name") and state.get("reachable")
                else None
            ),
            "port": state.get("port", OUTSIDE_PORT),
            "reachable": bool(state.get("reachable")),
            "problem": state.get("problem"),
            "checked": state.get("checked"),
            "certificate_expires": expiry(cert.read_bytes()) if cert.exists() else None,
            "listening": self.listener.running,
        }

    def proof(self, nonce):
        state = self.state()
        if not state.get("enabled") or not state.get("token"):
            return None
        token_hash = hashlib.sha256(state["token"].encode()).hexdigest()
        return hmac.new(token_hash.encode(), nonce.encode(), hashlib.sha256).hexdigest()

    # ── The service ───────────────────────────────────────────────────────
    def client(self):
        return httpx.AsyncClient(timeout=120, transport=self.transport)

    async def call(self, method, path, payload=None):
        state = self.state()
        headers = {"Authorization": f"Bearer {state['token']}"} if state.get("token") else {}
        try:
            async with self.client() as client:
                response = await client.request(
                    method, self.service + path, json=payload, headers=headers
                )
        except httpx.HTTPError as exc:
            raise ConnectError("Sparrow Connect can't be reached right now. Try again shortly.") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            raise ConnectError(detail or f"Sparrow Connect refused the request ({response.status_code}).")
        return response.json()

    async def register(self):
        home = await self.call("POST", "/api/v1/homes", {"version": 1})
        return self.save(
            id=home["id"], name=home["name"], link=home["link"], token=home["token"]
        )

    async def certificate(self):
        state = self.state()
        account = self.folder / "account.pem"
        if not account.exists():
            self.private("account.pem", new_key_pem())
        key = self.private("key.pem", new_key_pem())
        home = f"/api/v1/homes/{state['id']}/acme"

        async def publish(value):
            result = await self.call("PUT", home, {"value": value})
            if not result.get("ready"):
                raise ConnectError("The certificate proof didn't appear in DNS in time. Try again.")

        async def withdraw():
            with contextlib.suppress(ConnectError):
                await self.call("DELETE", home)

        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            acme = Acme(self.directory, account.read_bytes(), client)
            try:
                chain = await acme.certificate(state["name"], publish, withdraw, key.read_bytes())
            except (AcmeError, httpx.HTTPError) as exc:
                raise ConnectError(f"This server couldn't get its certificate. {exc}") from exc
        self.private("cert.pem", chain)

    async def public_address(self):
        configured = os.environ.get("SPARROW_PUBLIC_ADDRESS")
        if configured:
            return configured
        async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
            for url in ADDRESS_SERVICES:
                try:
                    text = (await client.get(url)).raise_for_status().text.strip()
                    ipaddress.ip_address(text)
                    return text
                except (httpx.HTTPError, ValueError):
                    continue
        raise ConnectError("This server couldn't find its internet address. Check its connection.")

    async def check_in(self):
        state = self.state()
        try:
            address = await self.public_address()
            result = await self.call(
                "POST",
                f"/api/v1/homes/{state['id']}/check-in",
                {"address": address, "port": state.get("port", OUTSIDE_PORT)},
            )
            return self.save(
                reachable=result["reachable"],
                problem=result["problem"],
                checked=time.time(),
            )
        except ConnectError as exc:
            return self.save(reachable=False, problem=str(exc), checked=time.time())

    async def serve(self):
        cert, key = self.folder / "cert.pem", self.folder / "key.pem"
        await self.listener.start(LISTEN_PORT, cert, key)

    # ── Switching ─────────────────────────────────────────────────────────
    async def enable(self, port):
        async with self.lock:
            self.save(enabled=True, port=port, problem=None)
            try:
                if not self.state().get("token"):
                    self.work = "Registering this server"
                    await self.register()
                cert = self.folder / "cert.pem"
                if not cert.exists() or expiry(cert.read_bytes()) - time.time() < RENEW_WITHIN:
                    self.work = "Getting this server's certificate"
                    await self.certificate()
                self.work = "Starting HTTPS"
                await self.serve()
                self.work = "Checking it can be reached from the internet"
                await self.check_in()
            except ConnectError as exc:
                self.save(reachable=False, problem=str(exc))
            except Exception as exc:
                log.exception("Sparrow Connect couldn't switch on")
                self.save(reachable=False, problem=f"Something went wrong switching on: {exc}")
            finally:
                self.work = None
            self.ensure_loop()

    async def disable(self):
        async with self.lock:
            state = self.state()
            self.work = "Turning off"
            try:
                if state.get("token"):
                    with contextlib.suppress(ConnectError):
                        await self.call("DELETE", f"/api/v1/homes/{state['id']}")
                await self.listener.stop()
                for name in ("cert.pem", "key.pem"):
                    with contextlib.suppress(FileNotFoundError):
                        (self.folder / name).unlink()
                self.state_path.unlink(missing_ok=True)
            finally:
                self.work = None
            if self.loop_task:
                self.loop_task.cancel()
                self.loop_task = None

    def ensure_loop(self):
        if not self.loop_task or self.loop_task.done():
            self.loop_task = asyncio.create_task(self.keep_up())

    async def keep_up(self):
        """Follow a changed internet address and renew the certificate."""
        while self.state().get("enabled"):
            await asyncio.sleep(CHECK_EVERY)
            try:
                async with self.lock:
                    cert = self.folder / "cert.pem"
                    if cert.exists() and expiry(cert.read_bytes()) - time.time() < RENEW_WITHIN:
                        await self.certificate()
                        await self.serve()
                    elif not self.listener.running and cert.exists():
                        await self.serve()
                    await self.check_in()
            except ConnectError as exc:
                self.save(problem=str(exc))
            except Exception:  # Keep the loop alive; the status shows the last problem.
                log.exception("Sparrow Connect upkeep failed")

    async def start(self):
        """At startup: resume serving if Connect was on."""
        state = self.state()
        if not state.get("enabled"):
            return
        if (self.folder / "cert.pem").exists():
            try:
                await self.serve()
            except ConnectError as exc:
                self.save(reachable=False, problem=str(exc))
            self.ensure_loop()
        else:
            self.begin(state.get("port", OUTSIDE_PORT))

    def begin(self, port):
        self.work = self.work or "Starting"
        return asyncio.create_task(self.enable(port))

    async def stop(self):
        if self.loop_task:
            self.loop_task.cancel()
        await self.listener.stop()


class Switch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    port: int | None = Field(default=None, ge=1, le=65535)


def install_connect(app, data_dir, connect=None):
    connect = connect or Connect(app, data_dir)
    router = APIRouter(prefix="/api/v1")

    @router.get("/admin/connect")
    def describe(request: Request):
        administrator(request)
        return connect.status()

    @router.put("/admin/connect")
    async def switch(body: Switch, request: Request):
        administrator(request)
        if connect.work:
            raise HTTPException(409, f"{connect.work}. Try again in a moment.")
        if body.enabled:
            connect.begin(body.port or connect.state().get("port") or OUTSIDE_PORT)
        else:
            connect.work = "Turning off"
            asyncio.create_task(connect.disable())
        return connect.status()

    @router.post("/admin/connect/check")
    async def check(request: Request):
        administrator(request)
        if not connect.state().get("enabled") or connect.work:
            return connect.status()
        async with connect.lock:
            await connect.check_in()
        return connect.status()

    @router.get("/connect/proof")
    def proof(nonce: str):
        """Answered to Sparrow Connect's call-back: only this server can make it."""
        if not 16 <= len(nonce) <= 128:
            raise HTTPException(422, "Invalid nonce.")
        value = connect.proof(nonce)
        if value is None:
            raise HTTPException(404, "Sparrow Connect is off on this server.")
        return {"proof": value}

    app.include_router(router)
    return connect
