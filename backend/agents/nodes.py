"""Durable coordinator for paired nodes and the local executor."""

import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .accounts import Accounts, digest
from .account_api import administrator
from .node_executor import Executor, NodeError, PROTOCOL, canonical


class Nodes:
    def __init__(self, storage, get_service):
        self.storage, self.get_service = storage, get_service
        self.accounts = Accounts(storage.data_dir)
        self._signals = {}
        self._local = None
        self._local_config = None
        with self.accounts.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    token_hash TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0,
                    last_seen REAL NOT NULL DEFAULT 0, capabilities TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS node_enrollment(token_hash TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL, name TEXT NOT NULL, expires REAL NOT NULL, used REAL);
                CREATE TABLE IF NOT EXISTS node_commands(id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_hash TEXT NOT NULL, result TEXT, state TEXT NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL, leased_until REAL NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS node_pending ON node_commands(node_id,state,created);
            """)

    def local(self):
        cfg = self.storage.get_config()
        config = {
            "roots": {"library": cfg.library_dir, "staging": cfg.staging_dir},
            "downloader": cfg.torrent_client.to_dict(),
        }
        if self._local is None or config != self._local_config:
            self._local = Executor(Path(self.storage.data_dir) / "local-node", **config)
            self._local_config = config
        return self._local

    def list(self):
        local = {
            "id": "local",
            "name": "This server",
            "online": True,
            "disabled": False,
            "capabilities": self.local().capabilities(),
            "last_seen": time.time(),
        }
        with self.accounts.connect() as db:
            rows = db.execute(
                "SELECT id,name,disabled,last_seen,capabilities FROM nodes ORDER BY name"
            ).fetchall()
        return [local] + [
            {
                **dict(r),
                "online": not r["disabled"] and time.time() - r["last_seen"] < 60,
                "capabilities": json.loads(r["capabilities"]),
            }
            for r in rows
        ]

    def info(self, node_id):
        return next((n for n in self.list() if n["id"] == node_id), None)

    def enroll(self, name, node_id=None):
        token = secrets.token_urlsafe(24)
        node_id = node_id or secrets.token_hex(12)
        with self.accounts.connect() as db:
            db.execute(
                "INSERT INTO node_enrollment VALUES (?, ?, ?, ?, NULL)",
                (
                    digest(token),
                    node_id,
                    name.strip()[:100] or "Storage node",
                    time.time() + 600,
                ),
            )
        return {"code": token, "expires_in_seconds": 600, "node_id": node_id}

    def pair(self, code, credentials, capabilities):
        if capabilities.get("protocol") != PROTOCOL:
            raise NodeError("This node version is not compatible with the server.")
        with self.accounts.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM node_enrollment WHERE token_hash=? AND expires>?",
                (digest(code), time.time()),
            ).fetchone()
            if not row:
                raise NodeError(
                    "Pairing code expired or was already used. Generate a new code in Storage."
                )
            if row["used"]:
                paired = db.execute(
                    "SELECT id FROM nodes WHERE id=? AND token_hash=? AND disabled=0",
                    (row["node_id"], digest(credentials)),
                ).fetchone()
                if paired:
                    return {"node_id": row["node_id"], "protocol": PROTOCOL}
                raise NodeError("This pairing code was already used.")
            # Client generates and persists its credential before pairing, so a
            # lost response cannot leave the node with an unknown server-side secret.
            db.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, 0, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "token_hash=excluded.token_hash, disabled=0, last_seen=excluded.last_seen, capabilities=excluded.capabilities",
                (
                    row["node_id"],
                    row["name"],
                    digest(credentials),
                    time.time(),
                    canonical(capabilities),
                ),
            )
            db.execute(
                "UPDATE node_enrollment SET used=? WHERE token_hash=?",
                (time.time(), digest(code)),
            )
        return {"node_id": row["node_id"], "protocol": PROTOCOL}

    def authenticate(self, token):
        if not token:
            return None
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT id FROM nodes WHERE token_hash=? AND disabled=0",
                (digest(token),),
            ).fetchone()
        return row["id"] if row else None

    def command_authorized(self, command):
        job_id = command.get("job_id")
        if not job_id:
            return True
        service = self.get_service()
        job = service.store.get_job(job_id) if service else None
        if not job or job.revision != command.get("revision"):
            return False
        # Explicit control operations may revoke work after changing job status.
        if command["kind"] in ("download_stop", "download_remove"):
            return True
        if job.status.value != "active":
            return False
        if job.user_id:
            user = self.accounts.user(job.user_id)
            return bool(
                user
                and user["role"] in ("admin", "requester")
                and self.accounts.can_access(user, job.library_id)
            )
        return not self.accounts.has_users()

    async def execute(
        self, node_id, kind, args=None, *, operation_id=None, job=None, timeout=60
    ):
        command = {
            "id": operation_id or secrets.token_hex(16),
            "kind": kind,
            "args": args or {},
            "job_id": job.id if job else "",
            "revision": job.revision if job else 0,
            "expires": time.time() + timeout,
        }
        if not self.command_authorized(command):
            raise NodeError("The request changed or its account no longer has access.")
        if node_id == "local" or not node_id:
            result = await self.local().execute(command)
            self.local().delivered(command["id"])
            if not operation_id and kind in (
                "read",
                "stat",
                "hls_segment",
                "subtitle_extract",
            ):
                with self.local().db() as db:
                    db.execute("DELETE FROM operations WHERE id=?", (command["id"],))
        else:
            node = self.info(node_id)
            if not node or node["disabled"]:
                raise NodeError("This storage node is no longer paired.")
            payload = canonical(
                {k: command[k] for k in ("kind", "args", "job_id", "revision")}
            )
            hashed = digest(payload)
            with self.accounts.connect() as db:
                existing = db.execute(
                    "SELECT * FROM node_commands WHERE id=?", (command["id"],)
                ).fetchone()
                if existing:
                    if (
                        existing["node_id"] != node_id
                        or existing["payload_hash"] != hashed
                    ):
                        raise NodeError("An operation ID cannot change its request.")
                    if existing["result"]:
                        result = json.loads(existing["result"])
                        if not result["ok"]:
                            raise NodeError(
                                result.get("error", "Node operation failed.")
                            )
                        return result["value"]
                else:
                    db.execute(
                        "INSERT INTO node_commands VALUES (?, ?, ?, ?, NULL, 'pending', ?, ?, 0)",
                        (
                            command["id"],
                            node_id,
                            canonical(command),
                            hashed,
                            time.time(),
                            command["expires"],
                        ),
                    )
            self._signals.setdefault(node_id, asyncio.Event()).set()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                with self.accounts.connect() as db:
                    row = db.execute(
                        "SELECT result FROM node_commands WHERE id=?", (command["id"],)
                    ).fetchone()
                if row and row["result"]:
                    result = json.loads(row["result"])
                    break
                await asyncio.sleep(0.05)
            else:
                raise NodeError(
                    "Storage did not respond in time. Work is recorded; reconnect the node and refresh its status."
                )
        if (
            node_id
            and node_id != "local"
            and not operation_id
            and kind in ("read", "stat", "hls_segment", "subtitle_extract")
        ):
            with self.accounts.connect() as db:
                db.execute(
                    "UPDATE node_commands SET result=? WHERE id=?",
                    (
                        canonical(
                            {
                                "ok": False,
                                "error": "This transient read was delivered. Issue a fresh read.",
                            }
                        ),
                        command["id"],
                    ),
                )
        if not result["ok"]:
            raise NodeError(result.get("error", "Node operation failed."))
        return result["value"]

    def heartbeat(self, node_id, capabilities):
        if capabilities.get("protocol") != PROTOCOL:
            raise NodeError("Node protocol is incompatible. Update the node.")
        with self.accounts.connect() as db:
            db.execute(
                "UPDATE nodes SET last_seen=?, capabilities=? WHERE id=?",
                (time.time(), canonical(capabilities), node_id),
            )

    def next_command(self, node_id):
        now = time.time()
        with self.accounts.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM node_commands WHERE node_id=? AND state='pending' AND leased_until<? ORDER BY created LIMIT 20",
                (node_id, now),
            ).fetchall()
            for row in rows:
                command = json.loads(row["payload"])
                if command["expires"] < now or not self.command_authorized(command):
                    db.execute(
                        "UPDATE node_commands SET state='done',result=? WHERE id=?",
                        (
                            canonical(
                                {
                                    "ok": False,
                                    "error": "Operation expired or request authority changed.",
                                }
                            ),
                            row["id"],
                        ),
                    )
                    continue
                db.execute(
                    "UPDATE node_commands SET leased_until=? WHERE id=?",
                    (now + 30, row["id"]),
                )
                return command
        return None

    def finish(self, node_id, operation_id, result):
        serialized = canonical(result)
        if len(serialized) > 16 * 1024 * 1024:
            raise NodeError("Node result exceeds the operation limit.")
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT result FROM node_commands WHERE id=? AND node_id=?",
                (operation_id, node_id),
            ).fetchone()
            if not row:
                raise NodeError("This operation does not belong to this node.")
            if row["result"] is None:
                db.execute(
                    "UPDATE node_commands SET state='done',result=? WHERE id=? AND node_id=?",
                    (serialized, operation_id, node_id),
                )
        return {"acknowledged": operation_id}


def install_nodes(app, storage, get_service):
    nodes = Nodes(storage, get_service)
    router = APIRouter(prefix="/api/v1")

    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class Enrollment(Input):
        name: str = Field(min_length=1, max_length=100)
        node_id: str | None = None

    class Pair(Input):
        code: str = Field(min_length=20, max_length=100)
        credential: str = Field(min_length=32, max_length=100)
        capabilities: dict

    class Poll(Input):
        capabilities: dict
        active_operations: list[str] = Field(default_factory=list, max_length=8)

    class Result(Input):
        result: dict

    def identity(request):
        authorization = request.headers.get("authorization", "")
        node_id = (
            nodes.authenticate(authorization.removeprefix("Bearer "))
            if authorization.startswith("Bearer ")
            else None
        )
        if not node_id:
            raise HTTPException(401, "Node credentials are invalid or revoked.")
        return node_id

    @router.get("/nodes")
    def list_nodes(request: Request):
        user = request.state.user
        return [n for n in nodes.list() if nodes.accounts.can_access(user, n["id"])]

    @router.post("/admin/nodes/enroll")
    def enroll(body: Enrollment, request: Request):
        administrator(request)
        if body.node_id and body.node_id == "local":
            raise HTTPException(422, "The local server cannot be paired as a node.")
        return nodes.enroll(body.name, body.node_id)

    @router.delete("/admin/nodes/{node_id}")
    def revoke(node_id: str, request: Request):
        administrator(request)
        with nodes.accounts.connect() as db:
            db.execute("UPDATE nodes SET disabled=1 WHERE id=?", (node_id,))
        return {"ok": True}

    @router.post("/node/pair")
    def pair(body: Pair):
        try:
            return nodes.pair(body.code, body.credential, body.capabilities)
        except NodeError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/node/poll")
    async def poll(body: Poll, request: Request):
        node_id = identity(request)
        with nodes.accounts.connect() as db:
            for operation_id in body.active_operations:
                db.execute(
                    "UPDATE node_commands SET leased_until=? WHERE id=? AND node_id=? AND state='pending'",
                    (time.time() + 30, operation_id, node_id),
                )
        try:
            nodes.heartbeat(node_id, body.capabilities)
        except NodeError as exc:
            raise HTTPException(409, str(exc)) from exc
        for _ in range(2):
            command = nodes.next_command(node_id)
            if command:
                return {"command": command}
            signal = nodes._signals.setdefault(node_id, asyncio.Event())
            signal.clear()
            try:
                await asyncio.wait_for(signal.wait(), 10)
            except asyncio.TimeoutError:
                pass
            identity(request)  # Recheck revocation after waiting.
        return {"command": None}

    @router.post("/node/results/{operation_id}")
    def result(operation_id: str, body: Result, request: Request):
        node_id = identity(request)
        try:
            return nodes.finish(node_id, operation_id, body.result)
        except NodeError as exc:
            raise HTTPException(409, str(exc)) from exc

    app.include_router(router)
    return nodes
