"""Changing where a computer keeps titles.

A computer's library folder (this server's or a paired computer's) changes in
one of three ways:

- A library that is empty, missing or not chosen yet just points at the new
  folder.
- "move": the new folder becomes the library straight away, so new titles go
  there. The old folder stays on as the computer's last library folder while
  each title in it moves across with the usual copy, check, switch and remove
  (a link on the same disk, so nothing is copied). Once it's empty, Sparrow
  stops using it. The files Sparrow doesn't know stay where they are.
- "both": the new folder is added beside the old one.

Library folders are numbered by position (library, library-2, …), so the old
folder always goes last: dropping it at the end renumbers nothing.
"""

import json
import logging
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .account_api import administrator
from .node_executor import NodeError, is_library
from .placement import PlacementError

logger = logging.getLogger(__name__)
ACTIVE = ("copying", "switching", "cleaning")
TABLE = (
    "CREATE TABLE IF NOT EXISTS storage_relocations(node_id TEXT PRIMARY KEY, "
    "data TEXT NOT NULL, created REAL NOT NULL)"
)


class FolderChange(ValueError):
    """Why a folder can't change now, in the household's words."""


def _number(root_id: str) -> int:
    return 1 if root_id == "library" else int(root_id.split("-")[1])


def _same(a: str, b: str) -> bool:
    clean = lambda p: (p or "").rstrip("/\\") or p
    return clean(a) == clean(b)


def _rows(accounts) -> list[tuple[str, dict]]:
    with accounts.connect() as db:
        db.execute(TABLE)
        rows = db.execute("SELECT node_id, data FROM storage_relocations").fetchall()
    return [(r["node_id"], json.loads(r["data"])) for r in rows]


def retiring(accounts) -> dict:
    """Library folders titles are moving out of: {(computer, folder): folder they go to}."""
    try:
        return {(node_id, data["from_root"]): "library" for node_id, data in _rows(accounts)}
    except Exception:  # No accounts database yet.
        return {}


def status(accounts, node_id: str) -> dict | None:
    """How a computer's titles are getting on moving to its new library folder."""
    try:
        found = next((data for nid, data in _rows(accounts) if nid == node_id), None)
        if not found:
            return None
        with accounts.connect() as db:
            left = db.execute(
                "SELECT COUNT(DISTINCT item_id) FROM assets WHERE node_id=? AND root_id=?",
                (node_id, found["from_root"]),
            ).fetchone()[0]
    except Exception:  # No catalogue yet.
        return None
    done = max(0, int(found.get("total") or 0) - left)
    return {
        "from_root": found["from_root"],
        "from_path": found["from_path"],
        "to_path": found["to_path"],
        "done": done,
        "total": done + left,
        "problem": found.get("problem", ""),
    }


class Relocations:
    def __init__(self, moves):
        self.moves = moves
        self.accounts, self.nodes, self.storage = moves.accounts, moves.nodes, moves.storage
        self.catalogue, self.get_service = moves.catalogue, moves.get_service
        self.on_change = None  # Called with a computer whose folders changed (Found looks).
        with self.accounts.connect() as db:
            db.execute(TABLE)

    # ─── Where a computer's folders are ──────────────────────────────

    def get(self, node_id):
        return next((data for nid, data in _rows(self.accounts) if nid == node_id), None)

    def current(self, node_id) -> dict:
        """A computer's library folders, main one first, and its incoming folder."""
        if node_id == "local":
            cfg = self.storage.get_config()
            return {
                "libraries": [p for p in [cfg.library_dir, *cfg.extra_library_dirs] if p],
                "staging": cfg.staging_dir or "",
            }
        node = self.nodes.info(node_id)
        roots = {
            r["id"]: r
            for r in (node["capabilities"].get("roots") or [] if node else [])
            if isinstance(r, dict) and r.get("id")
        }
        ids = sorted((k for k in roots if is_library(k)), key=_number)
        return {
            "libraries": [roots[k].get("label") or "" for k in ids],
            "staging": (roots.get("staging") or {}).get("label") or "",
        }

    def titles(self, node_id, root_id="library") -> tuple[int, int]:
        """How many titles a library folder holds, and their size."""
        try:
            with self.accounts.connect() as db:
                row = db.execute(
                    "SELECT COUNT(DISTINCT item_id), COALESCE(SUM(json_extract(data, '$.facts.size_bytes')), 0) "
                    "FROM assets WHERE node_id=? AND root_id=?",
                    (node_id, root_id),
                ).fetchone()
        except Exception:  # No catalogue yet.
            return 0, 0
        return int(row[0] or 0), int(row[1] or 0)

    def _available(self, node_id, root_id) -> bool:
        node = self.nodes.info(node_id)
        return any(
            isinstance(r, dict) and r.get("id") == root_id and r.get("available")
            for r in (node["capabilities"].get("roots") or [] if node else [])
        )

    def _moving(self, node_id) -> bool:
        """Whether a title is moving to or from this computer right now."""
        with self.accounts.connect() as db:
            rows = db.execute(
                f"SELECT data FROM storage_moves WHERE state IN ({','.join('?' * len(ACTIVE))})",
                ACTIVE,
            ).fetchall()
        for row in rows:
            data = json.loads(row["data"])
            if data["to_node"] == node_id or any(a["node_id"] == node_id for a in data["assets"]):
                return True
        return False

    async def _set(self, node_id, libraries, staging=None):
        """Use these folders on a computer, made there if they're missing."""
        if node_id == "local":
            from ..configuration import apply_config_update, settle_media_folders
            from ..models import SparrowConfig

            before = self.storage.get_config()
            changes = {
                "library_dir": libraries[0] if libraries else "",
                "extra_library_dirs": list(libraries[1:]),
            }
            if staging is not None:
                changes["staging_dir"] = staging
            try:
                config = apply_config_update(SparrowConfig.from_dict(before.to_dict()), changes)
                settle_media_folders(config, before)
            except (ValueError, TypeError) as exc:
                raise FolderChange(str(exc)) from None
            await self.storage.save_config(config)
        else:
            current = self.current(node_id)
            try:
                capabilities = await self.nodes.execute(
                    node_id,
                    "set_folders",
                    {
                        "libraries": libraries,
                        "staging": current["staging"] if staging is None else staging,
                        "keep_extra": False,
                    },
                    timeout=30,
                )
            except NodeError as exc:
                raise FolderChange(str(exc)) from None
            self.nodes.heartbeat(node_id, capabilities)
        self.nodes.observe_availability()
        if self.on_change:
            self.on_change(node_id)

    # ─── Changing them ───────────────────────────────────────────────

    async def change_library(self, user, node_id, path, titles=None):
        """Keep new titles in `path`. `titles` says what happens to those already kept."""
        path = (path or "").strip()
        if not path:
            raise FolderChange("Choose a folder.")
        if self.get(node_id):
            raise FolderChange("Titles are still moving to the new folder. Change it again once they have.")
        libraries = self.current(node_id)["libraries"]
        old = libraries[0] if libraries else ""
        if old and _same(old, path):
            return
        count, _ = self.titles(node_id)
        if not old or not count or not self._available(node_id, "library"):
            # Nothing to bring, or nothing reachable: a missing folder found elsewhere.
            await self._set(node_id, [path, *libraries[1:]])
            return
        if titles == "both":
            await self.add_library(node_id, path)
            return
        if titles != "move":
            raise FolderChange("Choose whether the titles already there move too.")
        if self._moving(node_id):
            raise FolderChange("Wait for the titles moving on this computer to finish, then try again.")
        extras = libraries[1:]
        old_root = f"library-{len(extras) + 2}"
        await self._set(node_id, [path, *extras, old])
        count, size = self.titles(node_id)
        with self.accounts.connect() as db:
            # The files stay where they are for now, in the folder's new place in the list.
            db.execute(
                "UPDATE assets SET root_id=? WHERE node_id=? AND root_id='library'", (old_root, node_id)
            )
            db.execute(TABLE)
            db.execute(
                "INSERT OR REPLACE INTO storage_relocations VALUES (?, ?, ?)",
                (
                    node_id,
                    json.dumps(
                        {
                            "from_root": old_root,
                            "from_path": old,
                            "to_path": path,
                            "total": count,
                            "bytes": size,
                            "user_id": (user or {}).get("id", ""),
                        }
                    ),
                    time.time(),
                ),
            )
        self.storage.operations.record("library_moving", library_id=node_id, subject=old_root)
        self.moves.wake.set()

    async def add_library(self, node_id, path):
        """Keep titles in another folder as well."""
        path = (path or "").strip()
        if not path:
            raise FolderChange("Choose a folder.")
        if self.get(node_id):
            raise FolderChange("Titles are moving to the new folder. Add another once they have.")
        libraries = self.current(node_id)["libraries"]
        if any(_same(p, path) for p in libraries):
            raise FolderChange("Sparrow already keeps titles there.")
        await self._set(node_id, [*libraries, path])

    async def change_incoming(self, node_id, path):
        """Download into `path` from now on. A download in progress finishes where it is."""
        path = (path or "").strip()
        if not path:
            raise FolderChange("Choose a folder.")
        libraries = self.current(node_id)["libraries"]
        if node_id != "local" and not libraries:
            raise FolderChange("Choose where titles are kept first.")
        await self._set(node_id, libraries, staging=path)

    # ─── Moving titles across ────────────────────────────────────────

    async def advance(self):
        """Start a move for each title still in an old folder; stop using it once it's empty."""
        for node_id, data in _rows(self.accounts):
            try:
                await self._advance(node_id, data)
            except (FolderChange, NodeError, PlacementError, KeyError, ValueError) as exc:
                logger.warning("library folder on %s: %s", node_id, exc)

    async def _advance(self, node_id, data):
        from_root = data["from_root"]
        with self.accounts.connect() as db:
            items = sorted(
                {
                    r["item_id"]
                    for r in db.execute(
                        "SELECT item_id FROM assets WHERE node_id=? AND root_id=?", (node_id, from_root)
                    )
                }
            )
            active = [
                (r["item_id"], json.loads(r["data"]))
                for r in db.execute(
                    f"SELECT item_id, data FROM storage_moves WHERE state IN ({','.join('?' * len(ACTIVE))})",
                    ACTIVE,
                )
            ]
        moving = {item_id for item_id, _ in active}
        service = self.get_service()
        jobs = [j for j in service.store.get_jobs() if j.status.value in ("active", "paused")] if service else []
        problem = ""
        for item_id in items:
            if item_id in moving:
                continue
            item = self.storage.get_library_item(item_id)
            if not item:
                continue
            library = item.metadata.get("library_id", "local")
            if any(
                j.tmdb_id == item.tmdb_id
                and j.media_type == item.media_type.value
                and (j.library_id or "local") == library
                for j in jobs
            ):
                continue  # Moves once its request finishes.
            assets = [
                a
                for a in self.catalogue.assets(None, item_id)
                if a["node_id"] == node_id and a["root_id"] == from_root
            ]
            if not assets:
                continue
            if any(a["state"] != "ready" for a in assets):
                problem = f"{item.title} isn't available, so it waits."
                continue
            try:
                self.moves._begin(
                    item, assets, node_id, "library", data.get("user_id", ""), relocation=node_id
                )
            except PlacementError as exc:
                problem = f"{item.title} can't move yet. {exc}"
        involved = any(
            d["to_node"] == node_id and any(a["root_id"] == from_root for a in d["assets"])
            for _, d in active
        )
        if not items and not involved:
            await self._finish(node_id, data)
        elif problem != data.get("problem", ""):
            data["problem"] = problem
            with self.accounts.connect() as db:
                db.execute(
                    "UPDATE storage_relocations SET data=? WHERE node_id=?", (json.dumps(data), node_id)
                )

    async def _finish(self, node_id, data):
        """The old folder is empty of titles: stop using it. Its other files stay."""
        libraries = self.current(node_id)["libraries"]
        if len(libraries) > 1 and _same(libraries[-1], data["from_path"]):
            await self._set(node_id, libraries[:-1])
        with self.accounts.connect() as db:
            db.execute("DELETE FROM storage_relocations WHERE node_id=?", (node_id,))
        self.storage.operations.record("library_moved", library_id=node_id, subject=data["from_root"])


def install_relocations(app, moves):
    relocations = Relocations(moves)
    moves.relocations = relocations
    nodes = moves.nodes
    router = APIRouter(prefix="/api/v1/admin/nodes")

    class Folder(BaseModel):
        model_config = ConfigDict(extra="forbid")
        path: str = Field(min_length=1, max_length=1024)

    class Library(Folder):
        titles: Literal["move", "both"] | None = None

    def reachable(node_id):
        if node_id == "local":
            return
        node = nodes.info(node_id)
        if not node or node["disabled"]:
            raise HTTPException(404, "This computer isn’t paired.")
        if not node["online"]:
            raise HTTPException(409, f"{node['name']} is offline. Wake it, then try again.")
        if not node["capabilities"].get("folders"):
            raise HTTPException(409, f"Set up {node['name']} again to choose its folders here.")

    async def changed(request, node_id, change):
        administrator(request)
        reachable(node_id)
        try:
            await change()
        except FolderChange as exc:
            raise HTTPException(422, str(exc)) from exc
        return nodes.computer(nodes.info(node_id))

    @router.put("/{node_id}/library")
    async def library(node_id: str, body: Library, request: Request):
        """Where a computer keeps new titles, and what happens to those it has."""
        return await changed(
            request,
            node_id,
            lambda: relocations.change_library(request.state.user, node_id, body.path, body.titles),
        )

    @router.post("/{node_id}/libraries")
    async def add_library(node_id: str, body: Folder, request: Request):
        """Another folder a computer keeps titles in."""
        return await changed(request, node_id, lambda: relocations.add_library(node_id, body.path))

    @router.put("/{node_id}/incoming")
    async def incoming(node_id: str, body: Folder, request: Request):
        """Where a computer downloads to."""
        return await changed(request, node_id, lambda: relocations.change_incoming(node_id, body.path))

    app.include_router(router)
    return relocations
