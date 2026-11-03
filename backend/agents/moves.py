"""Moving a title to other storage: copy, verify, switch, then remove the old copy.

The original stays until every file has a verified copy and the catalogue
points at it. Asset identities survive, so progress and subtitles follow.
"""

import asyncio
import json
import logging
import secrets
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .account_api import administrator
from .node_executor import NodeError, canonical
from .placement import PlacementError, headroom, plain_size

logger = logging.getLogger(__name__)
ACTIVE = ("copying", "switching", "cleaning")


class Moves:
    def __init__(self, accounts, nodes, storage, catalogue, placement, get_service):
        self.accounts, self.nodes, self.storage = accounts, nodes, storage
        self.catalogue, self.placement, self.get_service = catalogue, placement, get_service
        self.wake = asyncio.Event()
        with self.accounts.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS storage_moves(id TEXT PRIMARY KEY, item_id TEXT NOT NULL, "
                "state TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)"
            )

    def get(self, move_id):
        with self.accounts.connect() as db:
            row = db.execute("SELECT * FROM storage_moves WHERE id=?", (move_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, item_id=None):
        with self.accounts.connect() as db:
            rows = db.execute(
                "SELECT * FROM storage_moves"
                + (" WHERE item_id=?" if item_id else "")
                + " ORDER BY created DESC LIMIT 50",
                (item_id,) if item_id else (),
            ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row):
        data = json.loads(row["data"])
        files = [f for a in data["assets"] for f in a["files"]]
        return {
            "id": row["id"],
            "item_id": row["item_id"],
            "state": row["state"],
            "title": data["title"],
            "to": data["to_name"],
            "message": data.get("message", ""),
            "files": len(files),
            "copied": sum(1 for f in files if f.get("copied")),
            "bytes": sum(f["size_bytes"] for f in files),
            "created": row["created"],
            "updated": row["updated"],
        }

    def _save(self, move_id, state, data):
        with self.accounts.connect() as db:
            db.execute(
                "UPDATE storage_moves SET state=?, data=?, updated=? WHERE id=?",
                (state, json.dumps(data), time.time(), move_id),
            )

    # ─── Starting ────────────────────────────────────────────────────

    def start(self, user, item_id, destination):
        item = self.catalogue.item(user, item_id)
        if not item:
            raise PlacementError("That title isn't in the collection.")
        if any(m["item_id"] == item_id and m["state"] in ACTIVE for m in self.list(item_id)):
            raise PlacementError("This title is already moving.")
        library = item.metadata.get("library_id", "local")
        service = self.get_service()
        if service and any(
            j.tmdb_id == item.tmdb_id
            and j.media_type == item.media_type.value
            and j.library_id == library
            and j.status.value in ("active", "paused")
            for j in service.store.get_jobs()
        ):
            raise PlacementError(
                "A request for this title is still working here. Move it once the request finishes."
            )
        assets = self.catalogue.assets(None, item_id)
        if not assets:
            raise PlacementError("This title has no files to move.")
        offline = sorted({a["node_id"] for a in assets if a["state"] != "ready"})
        if offline:
            names = ", ".join(self.placement.describe(n) for n in offline)
            raise PlacementError(f"Some files are unavailable on {names}. Bring it back first.")
        total = sum(a["facts"]["size_bytes"] for a in assets)
        choice = self.placement.choose(
            user,
            destination,
            tmdb_id=item.tmdb_id,
            media_type=item.media_type.value,
            acquire=False,
            commit=False,
            needed=total,
            together=False,
            exclude={f"{a['node_id']}/{a['root_id']}" for a in assets},
        )
        to_node, to_root = choice["node_id"], choice["root_id"]
        moving = [a for a in assets if (a["node_id"], a["root_id"]) != (to_node, to_root)]
        if not moving:
            raise PlacementError(f"It's already on {choice['name']}.")
        disk = next(d for d in self.placement.disks() if d["key"] == f"{to_node}/{to_root}")
        need = sum(a["facts"]["size_bytes"] for a in moving)
        if disk["free_bytes"] is not None and disk["free_bytes"] < need + headroom(disk["total_bytes"]):
            raise PlacementError(
                f"{disk['name']} has {plain_size(disk['free_bytes'])} free; this title needs {plain_size(need)}."
            )
        with self.accounts.connect() as db:
            taken = [
                a["path"]
                for a in moving
                if db.execute(
                    "SELECT 1 FROM assets WHERE node_id=? AND root_id=? AND path=?",
                    (to_node, to_root, a["path"]),
                ).fetchone()
            ]
        if taken:
            raise PlacementError(f"{disk['name']} already has a file at {taken[0]}.")
        move_id = secrets.token_hex(8)
        data = {
            "title": item.title,
            "tmdb_id": item.tmdb_id,
            "media_type": item.media_type.value,
            "from_library": library,
            "to_node": to_node,
            "to_root": to_root,
            "to_name": disk["name"],
            "user_id": user["id"],
            "total": total,
            "assets": [
                {
                    "id": a["id"],
                    "node_id": a["node_id"],
                    "root_id": a["root_id"],
                    "files": [
                        {
                            "path": a["path"],
                            "size_bytes": a["facts"]["size_bytes"],
                            "version": a["facts"]["version"],
                        }
                    ],
                }
                for a in moving
            ],
            "message": f"Copying to {disk['name']}.",
        }
        with self.accounts.connect() as db:
            db.execute(
                "INSERT INTO storage_moves VALUES (?, ?, 'copying', ?, ?, ?)",
                (move_id, item_id, json.dumps(data), time.time(), time.time()),
            )
        self.wake.set()
        return self.get(move_id)

    # ─── Running ─────────────────────────────────────────────────────

    def online(self, node_id):
        node = self.nodes.info(node_id)
        return bool(node and node["online"] and not node["disabled"])

    async def advance(self, move_id):
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT item_id, state, data FROM storage_moves WHERE id=?", (move_id,)
            ).fetchone()
        state, data = row["state"], json.loads(row["data"])
        to_node, to_root = data["to_node"], data["to_root"]
        if state == "copying":
            for asset in data["assets"]:
                for waiting in {asset["node_id"], to_node}:
                    if not self.online(waiting):
                        data["message"] = f"Waiting for {self.placement.describe(waiting)} to come back."
                        self._save(move_id, state, data)
                        return False
                first = asset["files"][0]
                if "sha256" not in first:
                    facts = await self.nodes.execute(
                        asset["node_id"],
                        "checksum",
                        {"root_id": asset["root_id"], "path": first["path"], "version": first["version"]},
                        timeout=3600,
                    )
                    first["sha256"] = facts["sha256"]
                    companions = await self.nodes.execute(
                        asset["node_id"],
                        "companions",
                        {"root_id": asset["root_id"], "path": first["path"]},
                        timeout=600,
                    )
                    asset["files"] += [
                        {k: c[k] for k in ("path", "size_bytes", "version", "sha256")}
                        for c in companions
                    ]
                    self._save(move_id, state, data)
                for number, file in enumerate(asset["files"]):
                    if file.get("copied"):
                        continue
                    data["message"] = f"Copying to {data['to_name']}: {file['path']}"
                    self._save(move_id, state, data)
                    operation = f"mv-{move_id}-{asset['id'][:24]}-{number}"
                    args = {
                        "root_id": to_root,
                        "path": file["path"],
                        "size_bytes": file["size_bytes"],
                        "sha256": file["sha256"],
                    }
                    if asset["node_id"] == to_node:
                        args.update(from_root=asset["root_id"], from_path=file["path"], version=file["version"])
                    else:
                        args["source"] = self.nodes.open_transfer(
                            operation, to_node, asset["node_id"], asset["root_id"], file["path"], file["version"]
                        )
                    result = await self.nodes.execute(
                        to_node, "receive", args, operation_id=operation, timeout=12 * 3600
                    )
                    file.update(copied=True, placed=result["placed"], arrived=result["version"])
                    self._save(move_id, state, data)
            state = "switching"
            self._save(move_id, state, data)
        if state == "switching":
            await self.switch(row["item_id"], data)
            self.nodes.close_transfers(f"mv-{move_id}-")
            state = "cleaning"
            data["message"] = "Removing the old copy."
            self._save(move_id, state, data)
        if state == "cleaning":
            for asset in data["assets"]:
                if asset.get("released"):
                    continue
                if not self.online(asset["node_id"]):
                    data["message"] = (
                        f"Moved. The old copy is removed when {self.placement.describe(asset['node_id'])} is back."
                    )
                    self._save(move_id, state, data)
                    return False
                for number, file in enumerate(asset["files"]):
                    await self.nodes.execute(
                        asset["node_id"],
                        "release",
                        {"root_id": asset["root_id"], "path": file["path"], "sha256": file["sha256"]},
                        operation_id=f"rm-{move_id}-{asset['id'][:24]}-{number}",
                        timeout=600,
                    )
                asset["released"] = True
                self._save(move_id, state, data)
            data["message"] = f"Moved to {data['to_name']}."
            self._save(move_id, "done", data)
        return True

    async def switch(self, item_id, data):
        """Point the catalogue at the verified copies, keeping every identity."""
        to_node, to_root = data["to_node"], data["to_root"]
        moved = {a["id"] for a in data["assets"]}
        item = self.storage.get_library_item(item_id)
        target = None
        if to_node != data["from_library"]:
            target = self.catalogue.item_for(to_node, data["media_type"], data["tmdb_id"])
        arrived = {a["id"]: a["files"][0]["arrived"] for a in data["assets"]}
        with self.accounts.connect() as db:
            for asset in data["assets"]:
                row = db.execute("SELECT data FROM assets WHERE id=?", (asset["id"],)).fetchone()
                record = json.loads(row["data"])
                # The copy is the same bytes with its own file version.
                record["facts"]["version"] = arrived[asset["id"]]
                db.execute(
                    "UPDATE assets SET node_id=?, root_id=?, item_id=COALESCE(?, item_id), data=? WHERE id=?",
                    (to_node, to_root, target.id if target else None, canonical(record), asset["id"]),
                )
                try:
                    db.execute(
                        "UPDATE subtitle_tracks SET node_id=? WHERE asset_id=?", (to_node, asset["id"])
                    )
                except sqlite3.OperationalError:
                    pass  # No subtitles prepared yet.
            if to_node != data["from_library"]:
                try:
                    rows = db.execute(
                        "SELECT id FROM subscriptions WHERE media_type=? AND tmdb_id=? AND node_id=?",
                        (data["media_type"], data["tmdb_id"], data["from_library"]),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                for row in rows:
                    try:
                        db.execute("UPDATE subscriptions SET node_id=? WHERE id=?", (to_node, row["id"]))
                    except sqlite3.IntegrityError:
                        # That person already follows it there; that one carries on.
                        db.execute("DELETE FROM subscriptions WHERE id=?", (row["id"],))
        if not item:
            return

        def place(record):
            if record.get("asset_id") not in moved:
                return record
            relative = next(
                f["path"] for a in data["assets"] if a["id"] == record["asset_id"] for f in a["files"][:1]
            )
            record = {k: v for k, v in record.items() if k not in ("node_id", "available")}
            record["file_version"] = arrived[record["asset_id"]]
            if to_node == "local":
                record["path"] = str(self.nodes.local().path(to_root, relative))
            else:
                record.update(node_id=to_node, path=relative, available=True)
            return record

        if item.media_type.value == "movie":
            item.metadata = place(item.metadata)
            item.path = item.metadata.get("path", item.path)
        else:
            item.episodes = {
                season: {episode: place(r) for episode, r in episodes.items()}
                for season, episodes in item.episodes.items()
            }
        if target and target.id != item.id:
            for season, episodes in item.episodes.items():
                target.episodes.setdefault(season, {}).update(episodes)
            if item.media_type.value == "movie":
                target.metadata.update({k: v for k, v in item.metadata.items() if k != "library_id"})
                target.path = item.path
            await self.storage.add_library_item(target)
            await self.storage.delete_library_item(item.id)
        else:
            item.metadata["library_id"] = to_node
            await self.storage.add_library_item(item)

    async def run_pending(self):
        with self.accounts.connect() as db:
            rows = db.execute(
                "SELECT id FROM storage_moves WHERE state IN ('copying','switching','cleaning') ORDER BY created"
            ).fetchall()
        for row in rows:
            try:
                await self.advance(row["id"])
            except (NodeError, OSError, KeyError, ValueError) as exc:
                with self.accounts.connect() as db:
                    current = db.execute(
                        "SELECT state, data FROM storage_moves WHERE id=?", (row["id"],)
                    ).fetchone()
                data = json.loads(current["data"])
                data["message"] = f"Will try again shortly: {exc}"
                self._save(row["id"], current["state"], data)
                logger.warning("move %s paused: %s", row["id"], exc)

    async def loop(self):
        while True:
            self.wake.clear()
            try:
                await self.run_pending()
            except Exception:  # Never let one move stop the others for good.
                logger.exception("storage moves failed")
            try:
                await asyncio.wait_for(self.wake.wait(), 60)
            except asyncio.TimeoutError:
                pass


def install_moves(app, accounts, nodes, storage, catalogue, placement, get_service):
    moves = Moves(accounts, nodes, storage, catalogue, placement, get_service)
    router = APIRouter(prefix="/api/v1/admin/moves")

    class Start(BaseModel):
        model_config = ConfigDict(extra="forbid")
        item_id: str = Field(min_length=1, max_length=64)
        storage: str = Field(min_length=1, max_length=100)

    @router.get("")
    def listing(request: Request, item_id: str | None = None):
        administrator(request)
        return moves.list(item_id)

    @router.post("")
    def start(body: Start, request: Request):
        administrator(request)
        try:
            return moves.start(request.state.user, body.item_id, body.storage)
        except PlacementError as exc:
            raise HTTPException(422, str(exc)) from exc

    app.include_router(router)
    return moves
