"""Durable coordinator for paired nodes and the local executor."""

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .accounts import Accounts, digest
from .account_api import administrator
from . import computers, node_package
from .node_executor import (
    Executor, NodeError, PROTOCOL, TRANSIENT_KINDS, canonical, is_library, library_roots,
)



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
                CREATE TABLE IF NOT EXISTS node_transfers(token TEXT PRIMARY KEY, target TEXT NOT NULL,
                    source TEXT NOT NULL, root_id TEXT NOT NULL, path TEXT NOT NULL,
                    version TEXT NOT NULL, created REAL NOT NULL);
            """)
            columns = {r["name"] for r in db.execute("PRAGMA table_info(node_enrollment)")}
            if "progress" not in columns:
                # What the setup command on the other computer last reported.
                db.execute("ALTER TABLE node_enrollment ADD COLUMN progress TEXT")

    def local(self):
        cfg = self.storage.get_config()
        # Where this server's folders may be chosen; default: home, drives, mounts.
        places = [p for p in os.getenv("SPARROW_FOLDER_ROOTS", "").split(os.pathsep) if p]
        config = {
            "roots": {
                **library_roots([cfg.library_dir, *cfg.extra_library_dirs]),
                "staging": cfg.staging_dir,
            },
            "downloader": cfg.torrent_client.to_dict(),
        }
        if self._local is None or config != self._local_config:
            self._local = Executor(
                Path(self.storage.data_dir) / "local-node",
                **config,
                fetcher=lambda token, offset: self.transfer_bytes(token, "local", offset),
                exclude=[self.storage.data_dir],
                places=places or None,
            )
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

    def library_usage(self):
        """Bytes of verified library files on each disk, keyed (node, root)."""
        try:
            with self.accounts.connect() as db:
                rows = db.execute(
                    "SELECT node_id, root_id, SUM(json_extract(data, '$.facts.size_bytes')) AS used "
                    "FROM assets GROUP BY node_id, root_id"
                ).fetchall()
        except Exception:  # No catalogue yet.
            return {}
        return {(r["node_id"], r["root_id"]): int(r["used"] or 0) for r in rows}

    def library_titles(self, node_id):
        """How many titles each of a computer's library folders holds."""
        try:
            with self.accounts.connect() as db:
                rows = db.execute(
                    "SELECT root_id, COUNT(DISTINCT item_id) AS n FROM assets WHERE node_id=? GROUP BY root_id",
                    (node_id,),
                ).fetchall()
        except Exception:  # No catalogue yet.
            return {}
        return {r["root_id"]: int(r["n"]) for r in rows}

    def observe_availability(self):
        for node in self.list():
            if node["disabled"]:
                continue
            libraries = [
                root
                for root in (node["capabilities"].get("roots") or [])
                if isinstance(root, dict) and is_library(root.get("id"))
            ]
            available = (
                node["online"]
                and bool(libraries)
                and all(root.get("available", False) for root in libraries)
            )
            self.storage.operations.observe(
                "storage:" + node["id"],
                str(bool(available)),
                "storage_online" if available else "storage_offline",
                initial=not available,
                library_id=node["id"],
                title=node["name"],
            )

    def info(self, node_id):
        return next((n for n in self.list() if n["id"] == node_id), None)

    def jobs(self, node_id, capabilities=None):
        """The jobs the household wants a computer to do."""
        if capabilities is None:
            node = self.info(node_id)
            capabilities = node["capabilities"] if node else {}
        return computers.wanted(self.accounts, node_id, capabilities)

    def computer(self, node, used=None):
        """A computer as the Storage page shows it: jobs, health and usage."""
        if used is None:
            used = self.library_usage()
        kept = sum(v for (n, _), v in used.items() if n == node["id"])
        titles = self.library_titles(node["id"])
        for root in node["capabilities"].get("roots", []):
            if isinstance(root, dict) and is_library(root.get("id")):
                root["library_bytes"] = used.get((node["id"], root["id"]), 0)
                root["library_titles"] = titles.get(root["id"], 0)
        jobs = self.jobs(node["id"], node["capabilities"])
        node["library_bytes"] = kept
        from .relocation import status

        # Titles moving to a new library folder, shown on the computer's card.
        node["relocation"] = status(self.accounts, node["id"])
        from .found import summary

        # Titles found in its folders: added by themselves, or waiting for a name.
        node["found"] = summary(self.accounts, node["id"])
        from .release import current

        update = computers.update_status(
            node, current(), computers.settings(self.accounts, node["id"])
        )
        node["health"] = computers.health(node, jobs, update)
        return node

    def able(self, node_id, job):
        """Whether a computer is on for a job and can do it right now."""
        node = self.info(node_id)
        if not node or node["disabled"]:
            return False
        return computers.can(node, self.jobs(node_id, node["capabilities"]), job)

    def enroll(self, name="", node_id=None):
        token = secrets.token_urlsafe(24)
        while token[0] == "-":  # A leading dash reads as an option on a command line.
            token = secrets.token_urlsafe(24)
        node_id = node_id or secrets.token_hex(12)
        with self.accounts.connect() as db:
            db.execute(
                "INSERT INTO node_enrollment VALUES (?, ?, ?, ?, NULL, NULL)",
                # Unnamed computers take their own name when they pair.
                (digest(token), node_id, (name or "").strip()[:100], time.time() + 600),
            )
        return {"code": token, "expires_in_seconds": 600, "node_id": node_id}

    def enrollment(self, code):
        """The pairing a setup command belongs to, while its code is current."""
        with self.accounts.connect() as db:
            return db.execute(
                "SELECT * FROM node_enrollment WHERE token_hash=? AND expires>?",
                (digest(code), time.time()),
            ).fetchone()

    def report(self, code, stage):
        now = time.time()
        with self.accounts.connect() as db:
            db.execute(
                "UPDATE node_enrollment SET progress=?, expires=MAX(expires, ?) "
                "WHERE token_hash=? AND expires>?",
                # A setup that has started gets time to finish downloading Python.
                (stage, now + 900 if stage == "fetched" else 0, digest(code), now),
            )

    def pairing(self, node_id):
        """How the newest pairing for a computer is going, for the dialog watching it."""
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT * FROM node_enrollment WHERE node_id=? ORDER BY expires DESC LIMIT 1",
                (node_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "stage": "paired" if row["used"] and row["progress"] in (None, "pairing") else row["progress"],
            "paired": bool(row["used"]),
            # Set up by the one-line command, which says when the node is running.
            "scripted": row["progress"] is not None,
            "expires_in_seconds": max(0, int(row["expires"] - time.time())),
            "node": self.info(node_id) if row["used"] else None,
        }

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
                    "This pairing command expired or was already used. In Sparrow, open Settings > Computers > Add a computer for a fresh one."
                )
            if row["used"]:
                paired = db.execute(
                    "SELECT id FROM nodes WHERE id=? AND token_hash=? AND disabled=0",
                    (row["node_id"], digest(credentials)),
                ).fetchone()
                if paired:
                    return {"node_id": row["node_id"], "protocol": PROTOCOL}
                raise NodeError(
                    "This pairing command was already used. In Sparrow, open Settings > Computers > Add a computer for a fresh one."
                )
            # Client generates and persists its credential before pairing, so a
            # lost response cannot leave the node with an unknown server-side secret.
            db.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, 0, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "token_hash=excluded.token_hash, disabled=0, last_seen=excluded.last_seen, capabilities=excluded.capabilities",
                (
                    row["node_id"],
                    row["name"]
                    or str(capabilities.get("hostname") or "")[:100].strip()
                    or "Storage computer",
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
        # Explicit control operations may revoke work after changing job status,
        # and an ended request's own transfer folder may be cleared.
        if command["kind"] in ("download_stop", "download_remove"):
            return True
        if command["kind"] == "discard_staging" and job.status.value in ("complete", "abandoned"):
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
            if not operation_id and kind in TRANSIENT_KINDS:
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
            and kind in TRANSIENT_KINDS
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

    def open_transfer(self, token, target, source, root_id, path, version):
        """Let one computer read one library file from another, once moved."""
        with self.accounts.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO node_transfers VALUES (?, ?, ?, ?, ?, ?, ?)",
                (token, target, source, root_id, path, canonical(version), time.time()),
            )
        return token

    def close_transfers(self, prefix):
        with self.accounts.connect() as db:
            db.execute("DELETE FROM node_transfers WHERE token LIKE ?", (prefix + "%",))

    async def transfer_bytes(self, token, target, offset=0):
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT * FROM node_transfers WHERE token=? AND target=?", (token, target)
            ).fetchone()
        if not row:
            raise NodeError("This copy is not authorised for this computer.")
        version = json.loads(row["version"])
        size, offset = version["size_bytes"], max(0, int(offset))
        if row["source"] == "local":
            from .media_state import file_version

            path = self.local().path(row["root_id"], row["path"])
            if file_version(path) != version:
                raise NodeError("The original changed since it was checked.")
            with path.open("rb") as source:
                source.seek(offset)
                while chunk := await asyncio.to_thread(source.read, 1024 * 1024):
                    yield chunk
            return
        import base64

        while offset < size:
            result = await self.execute(
                row["source"],
                "read",
                {
                    "root_id": row["root_id"],
                    "path": row["path"],
                    "offset": offset,
                    "length": 1024 * 1024,
                    "version": version,
                },
                timeout=60,
            )
            data = base64.b64decode(result["bytes"])
            if not data:
                break
            offset += len(data)
            yield data

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
        name: str = Field(default="", max_length=100)
        node_id: str | None = None

    class Folders(Input):
        libraries: list[str] = Field(min_length=1, max_length=8)
        # Empty keeps the incoming folder it has; a storage-only computer has none.
        staging: str = Field(default="", max_length=1000)

    class NewFolder(Input):
        node_id: str = "local"
        path: str = Field(min_length=1, max_length=1000)
        name: str = Field(max_length=200)

    class Progress(Input):
        stage: str = Field(pattern=r"^[a-z]+(:[a-z]+)?$", max_length=40)

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

    class Jobs(Input):
        storage: bool | None = None
        downloads: bool | None = None
        subtitles: bool | None = None

    @router.get("/nodes")
    def list_nodes(request: Request):
        user = request.state.user
        used = nodes.library_usage()
        visible = [n for n in nodes.list() if nodes.accounts.can_access(user, n["id"])]
        return [nodes.computer(node, used) for node in visible]

    class Settings(Input):
        auto_update: bool

    @router.put("/admin/nodes/{node_id}/settings")
    def set_settings(node_id: str, body: Settings, request: Request):
        """Whether a computer updates itself when this server has a new version."""
        administrator(request)
        node = nodes.info(node_id)
        if not node or node["disabled"] or node_id == "local":
            raise HTTPException(404, "This computer isn’t paired.")
        computers.set_settings(nodes.accounts, node_id, auto_update=body.auto_update)
        nodes._signals.setdefault(node_id, asyncio.Event()).set()
        return nodes.computer(nodes.info(node_id))

    @router.post("/admin/nodes/{node_id}/update")
    def update_now(node_id: str, request: Request):
        """Ask a computer to take this server's version now and restart itself."""
        administrator(request)
        node = nodes.info(node_id)
        if not node or node["disabled"] or node_id == "local":
            raise HTTPException(404, "This computer isn’t paired.")
        computers.set_settings(nodes.accounts, node_id, update_requested=time.time())
        nodes._signals.setdefault(node_id, asyncio.Event()).set()
        return nodes.computer(nodes.info(node_id))

    @router.get("/node/package.tar.gz")
    async def node_package_download(request: Request):
        """This server's Sparrow Node, for a paired computer updating itself."""
        from fastapi.responses import Response

        identity(request)
        data = await asyncio.to_thread(node_package.package, "tar.gz")
        return Response(
            data,
            media_type="application/gzip",
            headers={"Cache-Control": "no-store", "X-Sparrow-Node-Version": node_package.version()},
        )

    @router.put("/admin/nodes/{node_id}/jobs")
    def set_jobs(node_id: str, body: Jobs, request: Request):
        """Turn a computer's jobs on or off; the computer follows on its next check."""
        administrator(request)
        node = nodes.info(node_id)
        if not node or node["disabled"]:
            raise HTTPException(404, "This computer isn’t paired.")
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            computers.set_wanted(nodes.accounts, node_id, changes, node["capabilities"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        nodes._signals.setdefault(node_id, asyncio.Event()).set()
        return nodes.computer(nodes.info(node_id))

    @router.post("/admin/nodes/enroll")
    def enroll(body: Enrollment, request: Request):
        administrator(request)
        if body.node_id and body.node_id == "local":
            raise HTTPException(422, "The local server cannot be paired as a node.")
        return nodes.enroll(body.name, body.node_id)

    @router.get("/admin/nodes/pairing/{node_id}")
    def pairing(node_id: str, request: Request):
        administrator(request)
        status = nodes.pairing(node_id)
        if not status:
            raise HTTPException(404, "No pairing for this computer.")
        return status

    def browsable(node_id):
        if node_id == "local":
            return
        node = nodes.info(node_id)
        if not node or node["disabled"]:
            raise HTTPException(404, "This computer isn’t paired.")
        if not node["online"]:
            raise HTTPException(409, f"{node['name']} is offline. Wake it, then try again.")
        if not node["capabilities"].get("folders"):
            raise HTTPException(
                409, f"Update Sparrow Node on {node['name']} to choose its folders here."
            )

    @router.get("/admin/storage/browse")
    async def browse_folders(request: Request, node_id: str = "local", path: str = ""):
        """Folders on a computer, for choosing where its library and downloads go."""
        administrator(request)
        browsable(node_id)
        try:
            return await nodes.execute(node_id, "browse", {"path": path}, timeout=20)
        except NodeError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/admin/storage/browse/folder")
    async def new_folder(body: NewFolder, request: Request):
        administrator(request)
        browsable(body.node_id)
        try:
            return await nodes.execute(
                body.node_id, "make_folder", {"path": body.path, "name": body.name}, timeout=20
            )
        except NodeError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/admin/nodes/{node_id}/folders")
    async def node_folders(node_id: str, body: Folders, request: Request):
        """Use these folders on a paired computer, made there if they're missing."""
        administrator(request)
        if node_id == "local":
            raise HTTPException(422, "Change this server’s folders in its own settings.")
        browsable(node_id)
        try:
            capabilities = await nodes.execute(
                node_id,
                "set_folders",
                {"libraries": body.libraries, "staging": body.staging},
                timeout=30,
            )
            nodes.heartbeat(node_id, capabilities)
        except NodeError as exc:
            raise HTTPException(422, str(exc)) from exc
        nodes.observe_availability()
        return nodes.info(node_id)

    def origin(request):
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        if scheme not in ("http", "https") or not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", request.url.netloc):
            raise HTTPException(400, "Open Sparrow by its address to pair a computer.")
        return f"{scheme}://{request.url.netloc}"

    @router.get("/node/setup/{name}")
    def setup_script(name: str, request: Request):
        """The one-command setup a computer runs: install, pair, start."""
        from fastapi.responses import PlainTextResponse

        code, _, kind = name.rpartition(".")
        if kind not in ("sh", "ps1") or not node_package.CODE.fullmatch(code):
            raise HTTPException(404, "Not found.")
        row = nodes.enrollment(code)
        valid = bool(row and not row["used"])
        if valid:
            nodes.report(code, "fetched")
        return PlainTextResponse(
            node_package.script(kind, origin(request), code, valid),
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/node/setup/{code}/package.{kind}")
    async def setup_package(code: str, kind: str):
        from fastapi.responses import Response

        if kind not in ("tar.gz", "zip") or not node_package.CODE.fullmatch(code) or not nodes.enrollment(code):
            raise HTTPException(404, "This setup command expired. Get a fresh one in Sparrow.")
        data = await asyncio.to_thread(node_package.package, kind)
        return Response(
            data,
            media_type="application/zip" if kind == "zip" else "application/gzip",
            headers={"Cache-Control": "no-store"},
        )

    def tools_response(platform):
        from fastapi.responses import FileResponse

        path = node_package.tools(platform)
        if not path:
            raise HTTPException(404, "This server has no media tools for this kind of computer.")
        return FileResponse(path, media_type="application/gzip", headers={"Cache-Control": "no-store"})

    @router.get("/node/setup/{code}/tools-{platform}.tar.gz")
    def setup_tools(code: str, platform: str):
        """Media tools and the download app for the computer running a setup command."""
        if not node_package.CODE.fullmatch(code) or not nodes.enrollment(code):
            raise HTTPException(404, "This setup command expired. Get a fresh one in Sparrow.")
        return tools_response(platform)

    @router.get("/node/tools/{platform}.tar.gz")
    def node_tools(platform: str, request: Request):
        """The same tools for a paired computer that is missing them."""
        identity(request)
        return tools_response(platform)

    @router.post("/node/setup/{code}/progress")
    def setup_progress(code: str, body: Progress):
        if node_package.CODE.fullmatch(code):
            nodes.report(code, body.stage)
        return {"ok": True}

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
        # Every answer says which jobs this computer should be doing, and
        # which version of Sparrow Node is current.
        from .release import current

        jobs = nodes.jobs(node_id, body.capabilities)
        offer = computers.update_offer(nodes.accounts, node_id, current())

        def answer(command):
            return {
                "command": command,
                "jobs": nodes.jobs(node_id, body.capabilities),
                "update": computers.update_offer(nodes.accounts, node_id, current()),
            }

        for _ in range(2):
            command = nodes.next_command(node_id)
            if command:
                return answer(command)
            if (
                nodes.jobs(node_id, body.capabilities) != jobs
                or computers.update_offer(nodes.accounts, node_id, current()) != offer
            ):
                return answer(None)
            signal = nodes._signals.setdefault(node_id, asyncio.Event())
            signal.clear()
            try:
                await asyncio.wait_for(signal.wait(), 10)
            except asyncio.TimeoutError:
                pass
            identity(request)  # Recheck revocation after waiting.
        return answer(None)

    @router.get("/node/transfers/{token}")
    async def transfer(token: str, request: Request, offset: int = 0):
        from fastapi.responses import StreamingResponse

        node_id = identity(request)
        stream = nodes.transfer_bytes(token, node_id, offset)
        try:
            first = await anext(stream, b"")
        except NodeError as exc:
            raise HTTPException(409, str(exc)) from exc

        async def body():
            yield first
            async for chunk in stream:
                yield chunk

        return StreamingResponse(body(), media_type="application/octet-stream")

    @router.get("/node/models")
    async def node_models(request: Request):
        """Speech models this server shares with a computer setting up Subtitles."""
        from .speech_setup import shared_models

        identity(request)
        models = await asyncio.to_thread(shared_models, nodes.local().data_dir)
        return [{k: m[k] for k in ("name", "files")} for m in models]

    @router.get("/node/models/{name}/{file}")
    async def node_model_file(name: str, file: str, request: Request, offset: int = 0):
        from fastapi.responses import StreamingResponse
        from .speech_setup import shared_file

        identity(request)
        path = await asyncio.to_thread(shared_file, nodes.local().data_dir, name, file)
        if not path:
            raise HTTPException(404, "This server doesn't share that model file.")
        size = path.stat().st_size
        offset = max(0, min(int(offset), size))

        async def body():
            with path.open("rb") as source:
                source.seek(offset)
                while chunk := await asyncio.to_thread(source.read, 1024 * 1024):
                    yield chunk

        return StreamingResponse(
            body(), media_type="application/octet-stream",
            headers={"Content-Length": str(size - offset)},
        )

    @router.post("/node/results/{operation_id}")
    def result(operation_id: str, body: Result, request: Request):
        node_id = identity(request)
        try:
            return nodes.finish(node_id, operation_id, body.result)
        except NodeError as exc:
            raise HTTPException(409, str(exc)) from exc

    app.include_router(router)
    return nodes
