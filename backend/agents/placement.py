"""Where new media is stored: Automatic, a pool, a person's storage or one place.

A destination is resolved once, when a request starts, and pinned to the job.
Placement reads reported capacity; publication still checks the real bytes.
"""

from __future__ import annotations

import json
import re
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .account_api import administrator
from .accounts import DESTINATION
from .node_executor import is_library as is_library_root

RULES = ("most_free", "fill", "round_robin")
GB = 1024**3
# Typical size of one file at the preferred quality: (film, episode), in GB.
# Only used to skip a disk that plainly cannot hold the request.
TYPICAL_GB = {
    "480p": (2, 0.5),
    "720p": (5, 1.2),
    "1080p": (12, 2.5),
    "2160p": (40, 8),
}


class PlacementError(ValueError):
    pass


def disk_word(root) -> str:
    """A disk's folder name, else its number on that computer."""
    label = str(root.get("label") or "").replace("\\", "/").rstrip("/")
    number = (root["id"].partition("-")[2] or "1")
    return label.rpartition("/")[2] or f"Disk {number}"


def expected_bytes(values, media_type, count) -> int:
    film, episode = TYPICAL_GB.get(values.get("preferred_quality", "1080p"), (12, 2.5))
    each = film if media_type == "movie" else episode
    cap = float(values.get("max_file_size_gb") or 0)
    if cap:
        each = min(each, cap)
    return int(each * max(1, count) * GB)


def headroom(total) -> int:
    return max(5 * GB, int((total or 0) * 0.02))


def plain_size(value) -> str:
    gb = (value or 0) / GB
    return f"{gb / 1024:.1f} TB" if gb >= 1024 else f"{gb:.0f} GB"


class Placement:
    def __init__(self, accounts, nodes, storage, get_service):
        self.accounts, self.nodes = accounts, nodes
        self.storage, self.get_service = storage, get_service
        with self.accounts.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS storage_pools(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "INSERT OR IGNORE INTO storage_pools VALUES ('auto', ?)",
                (json.dumps({"rule": "most_free", "cursor": -1}),),
            )

    # ─── Storage the household has ───────────────────────────────────

    def disks(self):
        """Every library folder on every paired computer, in a stable order."""
        out = []
        for node in self.nodes.list():
            if node["disabled"]:
                continue
            roots = [
                r
                for r in node["capabilities"].get("roots") or []
                if isinstance(r, dict) and is_library_root(r.get("id"))
            ]
            words = [disk_word(r) for r in roots]
            if len(set(words)) < len(words):
                # Two folders with one name: show where each one is.
                words = [r.get("label") or disk_word({"id": r["id"]}) for r in roots]
            for root, word in zip(roots, words):
                out.append(
                    {
                        "key": f"{node['id']}/{root['id']}",
                        "node_id": node["id"],
                        "root_id": root["id"],
                        "computer": node["name"],
                        "name": node["name"]
                        if len(roots) == 1
                        else f"{node['name']} · {word}",
                        "label": root.get("label") or "",
                        "online": bool(node["online"]),
                        "available": bool(node["online"] and root.get("available")),
                        "writable": root.get("writable", True) is not False,
                        "free_bytes": root.get("free_bytes"),
                        "total_bytes": root.get("total_bytes"),
                        "download": bool(node["capabilities"].get("download")),
                    }
                )
        return out

    def settings(self):
        with self.accounts.connect() as db:
            row = db.execute("SELECT data FROM storage_pools WHERE id='auto'").fetchone()
        return {"rule": json.loads(row["data"]).get("rule", "most_free")}

    def set_rule(self, rule):
        if rule not in RULES:
            raise PlacementError("Choose most free space, fill in order or take turns.")
        self._update("auto", rule=rule, cursor=-1)
        return self.settings()

    def pools(self):
        with self.accounts.connect() as db:
            rows = db.execute(
                "SELECT id, data FROM storage_pools WHERE id!='auto'"
            ).fetchall()
        pools = [{"id": r["id"], **json.loads(r["data"])} for r in rows]
        for pool in pools:
            pool.pop("cursor", None)
        return sorted(pools, key=lambda p: p["name"].lower())

    def pool(self, pool_id):
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT data FROM storage_pools WHERE id=? AND id!='auto'", (pool_id,)
            ).fetchone()
        return {"id": pool_id, **json.loads(row["data"])} if row else None

    def save_pool(self, pool_id, name, rule, members):
        name = name.strip()
        if not 1 <= len(name) <= 60:
            raise PlacementError("Give the pool a name of up to 60 characters.")
        if rule not in RULES:
            raise PlacementError("Choose most free space, fill in order or take turns.")
        known = {d["key"] for d in self.disks()}
        members = list(dict.fromkeys(members))
        if not members:
            raise PlacementError("Add at least one disk to the pool.")
        if pool_id:
            current = self.pool(pool_id)
            if not current:
                raise PlacementError("That pool no longer exists.")
            # Members of a computer that is offline right now stay listed.
            known |= set(current["members"])
        unknown = [m for m in members if m not in known]
        if unknown:
            raise PlacementError("Choose disks Sparrow can see.")
        pool_id = pool_id or secrets.token_hex(6)
        with self.accounts.connect() as db:
            db.execute(
                "INSERT INTO storage_pools VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (
                    pool_id,
                    json.dumps(
                        {"name": name, "rule": rule, "members": members, "cursor": -1}
                    ),
                ),
            )
        return self.pool(pool_id)

    def delete_pool(self, pool_id):
        with self.accounts.connect() as db:
            db.execute(
                "DELETE FROM storage_pools WHERE id=? AND id!='auto'", (pool_id,)
            )

    def _update(self, pool_id, **changes):
        with self.accounts.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT data FROM storage_pools WHERE id=?", (pool_id,)
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE storage_pools SET data=? WHERE id=?",
                    (json.dumps({**json.loads(row["data"]), **changes}), pool_id),
                )

    def destinations(self, user):
        """What a person can choose in Store on, Preferences and Defaults."""
        disks = [d for d in self.disks() if self.accounts.can_access(user, d["node_id"])]
        computers = []
        for disk in disks:
            computer = next(
                (c for c in computers if c["value"] == disk["node_id"]), None
            )
            if not computer:
                computer = {
                    "value": disk["node_id"],
                    "kind": "computer",
                    "name": disk["computer"],
                    "online": disk["online"],
                    "disks": [],
                }
                computers.append(computer)
            computer["disks"].append(
                {
                    "value": disk["key"],
                    "kind": "disk",
                    "name": disk["name"],
                    "online": disk["available"],
                    "free_bytes": disk["free_bytes"],
                    "total_bytes": disk["total_bytes"],
                    "download": disk["download"],
                }
            )
        visible = {d["key"] for d in disks}
        return {
            "rule": self.settings()["rule"],
            "computers": computers,
            "pools": [
                {
                    "value": f"pool:{p['id']}",
                    "kind": "pool",
                    "name": p["name"],
                    "rule": p["rule"],
                    "members": [m for m in p["members"] if m in visible],
                }
                for p in self.pools()
                if any(m in visible for m in p["members"])
            ],
        }

    def describe(self, value):
        """A destination in a few plain words."""
        if value in ("", "auto"):
            return "Automatic"
        if value == "ask":
            return "Ask each time"
        if value.startswith("pool:"):
            pool = self.pool(value[5:])
            return pool["name"] if pool else "A removed pool"
        disk = next((d for d in self.disks() if d["key"] == value), None)
        if disk:
            return disk["name"]
        node = self.nodes.info(value)
        return node["name"] if node and not node["disabled"] else "Removed storage"

    # ─── Choosing ────────────────────────────────────────────────────

    def home(self, tmdb_id, media_type):
        """Disks already holding this title or working on it, most first."""
        counts = {}
        items = [
            i.id
            for i in self.storage.get_library()
            if i.tmdb_id == tmdb_id and i.media_type.value == media_type
        ]
        if items:
            with self.accounts.connect() as db:
                rows = db.execute(
                    f"SELECT node_id, root_id, COUNT(*) AS n FROM assets WHERE item_id IN ({','.join('?' * len(items))}) GROUP BY node_id, root_id",
                    items,
                ).fetchall()
            for row in rows:
                if is_library_root(row["root_id"]):
                    key = f"{row['node_id']}/{row['root_id']}"
                    counts[key] = counts.get(key, 0) + row["n"]
        service = self.get_service()
        for job in service.store.get_jobs() if service else []:
            if (
                job.tmdb_id == tmdb_id
                and job.media_type == media_type
                and job.status.value in ("active", "paused")
            ):
                key = f"{job.node_id or 'local'}/{job.root_id or 'library'}"
                counts[key] = counts.get(key, 0) + 1
        return sorted(counts, key=lambda k: -counts[k])

    def members(self, destination):
        """(disks, rule, scope words, explicit single disk?) for a destination."""
        disks = self.disks()
        if destination == "auto":
            return disks, self.settings()["rule"], "your storage", False
        if destination.startswith("pool:"):
            pool = self.pool(destination[5:])
            if not pool:
                raise PlacementError(
                    "That storage pool no longer exists. Choose another place."
                )
            order = {key: n for n, key in enumerate(pool["members"])}
            chosen = sorted(
                (d for d in disks if d["key"] in order), key=lambda d: order[d["key"]]
            )
            return chosen, pool["rule"], pool["name"], False
        node_id, _, root_id = destination.partition("/")
        node = self.nodes.info(node_id)
        if not node or node["disabled"]:
            raise PlacementError("That storage is no longer paired. Choose another place.")
        chosen = [
            d
            for d in disks
            if d["node_id"] == node_id and (not root_id or d["root_id"] == root_id)
        ]
        if not chosen and root_id:
            if node["online"]:
                raise PlacementError(
                    f"{node['name']} no longer offers that disk. Choose another place."
                )
            # An offline computer reports no disks; a pinned choice waits for it.
            chosen = [
                {
                    "key": destination,
                    "node_id": node_id,
                    "root_id": root_id,
                    "computer": node["name"],
                    "name": node["name"],
                    "online": False,
                    "available": False,
                    "writable": True,
                    "free_bytes": None,
                    "total_bytes": None,
                    "download": bool(node["capabilities"].get("download")),
                }
            ]
        if not chosen:
            chosen = [
                {
                    "key": f"{node_id}/library",
                    "node_id": node_id,
                    "root_id": "library",
                    "computer": node["name"],
                    "name": node["name"],
                    "online": bool(node["online"]),
                    "available": False,
                    "writable": True,
                    "free_bytes": None,
                    "total_bytes": None,
                    "download": bool(node["capabilities"].get("download")),
                }
            ]
        return chosen, "most_free", node["name"], bool(root_id) or len(chosen) == 1

    def choose(
        self,
        user,
        destination,
        *,
        tmdb_id,
        media_type,
        count=1,
        values=None,
        source="request",
        acquire=True,
        commit=True,
        needed=None,
        together=True,
        exclude=(),
    ):
        """Pick one disk for new work and say why, in one plain line."""
        destination = destination or "auto"
        if not DESTINATION.fullmatch(destination):
            raise PlacementError("Choose a storage place from the list.")
        if destination == "ask":
            raise PlacementError("Choose where to store this.")
        candidates, rule, scope, single = self.members(destination)
        candidates = [
            d
            for d in candidates
            if self.accounts.can_access(user, d["node_id"])
            and (single or d["key"] not in exclude)
        ]
        if not candidates:
            raise PlacementError("None of this storage is available to your account.")
        if needed is None:
            needed = expected_bytes(values or {}, media_type, count)
        film = media_type == "movie"

        def problem(disk):
            if not disk["available"]:
                return f"{disk['name']} is offline"
            if not disk["writable"]:
                return f"Sparrow can't save to {disk['name']}"
            if acquire and not disk["download"]:
                return f"{disk['name']} has no download app"
            free = disk["free_bytes"]
            if free is not None and free < needed + headroom(disk["total_bytes"]):
                return f"{disk['name']} is nearly full ({plain_size(free)} free)"
            return ""

        def picked(disk, reason):
            return {
                "node_id": disk["node_id"],
                "root_id": disk["root_id"],
                "name": disk["name"],
                "destination": destination,
                "reason": reason,
            }

        if single:
            disk = candidates[0]
            why = problem(disk)
            whose = {
                "request": "as chosen",
                "personal": "your storage",
                "server": "the household's storage",
            }.get(source, "as chosen")
            if not disk["online"] or why.endswith("is offline"):
                return picked(
                    disk,
                    f"On {disk['name']}, {whose}. It's offline, so this starts when it's back.",
                )
            if why:
                raise PlacementError(
                    f"{why}, and this needs about {plain_size(needed)}. Free some space or choose another place."
                    if "full" in why
                    else f"{why}. Choose another place."
                )
            return picked(disk, f"On {disk['name']}, {whose}.")

        by_key = {d["key"]: d for d in candidates}
        notes = []
        for key in self.home(tmdb_id, media_type) if together else ():
            disk = by_key.get(key)
            if not disk:
                continue
            together = (
                "with the other copy" if film else "with the rest of the show"
            )
            why = problem(disk)
            if not why:
                return picked(disk, f"Kept {together} on {disk['name']}.")
            if not disk["online"]:
                # Waiting keeps a show in one place; a new copy elsewhere splits it.
                return picked(
                    disk,
                    f"Kept {together} on {disk['name']}. It's offline, so this starts when it's back.",
                )
            notes.append(why)
            break
        eligible = [d for d in candidates if not problem(d)]
        if not eligible:
            reasons = "; ".join(problem(d) for d in candidates)
            raise PlacementError(
                f"No storage in {scope} can take this right now: {reasons}."
            )
        if len(candidates) == 1:
            chosen, reason = eligible[0], f"On {eligible[0]['name']}."
        elif rule == "fill":
            chosen = eligible[0]
            skipped = [problem(d) for d in candidates[: candidates.index(chosen)]]
            notes += [s for s in skipped if s not in notes]
            reason = f"On {chosen['name']}, the first in order with room."
        elif rule == "round_robin":
            pool_id = destination[5:] if destination.startswith("pool:") else "auto"
            with self.accounts.connect() as db:
                row = db.execute(
                    "SELECT data FROM storage_pools WHERE id=?", (pool_id,)
                ).fetchone()
            cursor = json.loads(row["data"]).get("cursor", -1) if row else -1
            order = candidates[cursor + 1 :] + candidates[: cursor + 1]
            chosen = next(d for d in order if d in eligible)
            skipped = [problem(d) for d in order[: order.index(chosen)]]
            notes += [s for s in skipped if s not in notes]
            if commit:
                self._update(pool_id, cursor=candidates.index(chosen))
            reason = f"On {chosen['name']}, next in turn."
        else:
            chosen = max(eligible, key=lambda d: d["free_bytes"] or 0)
            reason = f"On {chosen['name']}, which has the most free space."
        if notes:
            reason = reason[:-1] + " — " + "; ".join(notes) + "."
        return picked(chosen, reason)

    def root_on(self, node_id, tmdb_id, media_type):
        """A disk on one computer for follow-up work, such as new episodes."""
        disks = [d for d in self.disks() if d["node_id"] == (node_id or "local")]
        keys = {d["key"]: d for d in disks}
        for key in self.home(tmdb_id, media_type):
            if key in keys:
                return keys[key]["root_id"]
        usable = [d for d in disks if d["available"] and d["writable"]]
        if usable:
            return max(usable, key=lambda d: d["free_bytes"] or 0)["root_id"]
        return "library"


def install_placement(app, accounts, nodes, storage, get_service):
    placement = Placement(accounts, nodes, storage, get_service)
    router = APIRouter(prefix="/api/v1")

    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class Rule(Input):
        rule: str

    class Pool(Input):
        name: str = Field(min_length=1, max_length=60)
        rule: str = "most_free"
        members: list[str] = Field(default_factory=list, max_length=64)

    class Preview(Input):
        tmdb_id: int = Field(gt=0)
        media_type: str
        count: int = Field(default=1, ge=1, le=2000)
        storage: str | None = None

    def fail(exc):
        raise HTTPException(422, str(exc)) from exc

    @router.get("/storage/destinations")
    def destinations(request: Request):
        return placement.destinations(request.state.user)

    @router.post("/storage/preview")
    def preview(body: Preview, request: Request):
        """Where a request would go now, without reserving a turn."""
        user = request.state.user
        prefs = accounts.resolve(user["id"])
        value = body.storage or prefs["values"]["storage"]
        source = "request" if body.storage else prefs["sources"].get("storage", "server")
        try:
            return {
                **placement.choose(
                    user,
                    value,
                    tmdb_id=body.tmdb_id,
                    media_type=body.media_type,
                    count=body.count,
                    values=prefs["values"],
                    source=source,
                    commit=False,
                ),
                "label": placement.describe(value),
            }
        except PlacementError as exc:
            return {"error": str(exc), "destination": value, "label": placement.describe(value)}

    @router.put("/admin/storage/rule")
    def rule(body: Rule, request: Request):
        administrator(request)
        try:
            return placement.set_rule(body.rule)
        except PlacementError as exc:
            fail(exc)

    @router.get("/admin/storage/pools")
    def pools(request: Request):
        administrator(request)
        return placement.pools()

    @router.post("/admin/storage/pools")
    def create_pool(body: Pool, request: Request):
        administrator(request)
        try:
            return placement.save_pool(None, body.name, body.rule, body.members)
        except PlacementError as exc:
            fail(exc)

    @router.put("/admin/storage/pools/{pool_id}")
    def update_pool(pool_id: str, body: Pool, request: Request):
        administrator(request)
        try:
            return placement.save_pool(pool_id, body.name, body.rule, body.members)
        except PlacementError as exc:
            fail(exc)

    @router.delete("/admin/storage/pools/{pool_id}")
    def delete_pool(pool_id: str, request: Request):
        administrator(request)
        placement.delete_pool(pool_id)
        return {"ok": True}

    app.include_router(router)
    return placement
