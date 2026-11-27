"""Where new media is stored: Automatic, a pool, a person's storage or one place.

A destination is resolved once, when a request starts, and pinned to the job.
Placement reads reported capacity; publication still checks the real bytes.
"""

import json
import re
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import computers
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
            jobs = computers.wanted(self.accounts, node["id"], node["capabilities"])
            # Downloads count only where they're on and working right now.
            downloads = computers.can(node, jobs, "downloads")
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
                        "download": downloads,
                        # Storage off: keeps what it has, takes nothing new.
                        "storage": bool(jobs.get("storage")),
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
        disks = [
            d
            for d in self.disks()
            if d["storage"] and self.accounts.can_access(user, d["node_id"])
        ]
        listed = []
        for disk in disks:
            computer = next(
                (c for c in listed if c["value"] == disk["node_id"]), None
            )
            if not computer:
                computer = {
                    "value": disk["node_id"],
                    "kind": "computer",
                    "name": disk["computer"],
                    "online": disk["online"],
                    "disks": [],
                }
                listed.append(computer)
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
            "computers": listed,
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
                if getattr(job, "keep_node_id", ""):
                    # Downloading elsewhere, kept here once it's done.
                    key = f"{job.keep_node_id}/{job.keep_root_id or 'library'}"
                else:
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
                    "download": False,
                    "storage": True,
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
                    "download": False,
                    "storage": True,
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
        """Pick one disk to keep new work on, where to download it, and say why.

        The kept disk answers node_id/root_id unless another computer has to
        download it: then node_id/root_id is where it downloads and lands, and
        keep_node_id/keep_root_id is where it moves once the request is done.
        """
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
        resting = [d for d in candidates if not d.get("storage", True)]
        candidates = [d for d in candidates if d.get("storage", True)]
        if not candidates:
            names = " and ".join(sorted({d["computer"] for d in resting}))
            raise PlacementError(
                f"Storage is off on {names}. Turn it on in Storage, or choose another place."
            )
        if needed is None:
            needed = expected_bytes(values or {}, media_type, count)
        film = media_type == "movie"

        def problem(disk):
            if not disk["available"]:
                return f"{disk['name']} is offline"
            if not disk["writable"]:
                return f"Sparrow can't save to {disk['name']}"
            free = disk["free_bytes"]
            if free is not None and free < needed + headroom(disk["total_bytes"]):
                return f"{disk['name']} is nearly full ({plain_size(free)} free)"
            return ""

        def downloader(keep):
            """Where to download for a title kept on `keep`: there, or the roomiest computer that can."""
            if not acquire or keep["download"]:
                return None
            others = [
                d
                for d in self.disks()
                if d["download"]
                and d["node_id"] != keep["node_id"]
                and self.accounts.can_access(user, d["node_id"])
                and not problem(d)
            ]
            return max(others, key=lambda d: d["free_bytes"] or 0) if others else None

        def picked(disk, reason, landing=None):
            choice = {
                "node_id": disk["node_id"],
                "root_id": disk["root_id"],
                "name": disk["name"],
                "destination": destination,
                "reason": reason,
            }
            if landing:
                choice.update(
                    node_id=landing["node_id"],
                    root_id=landing["root_id"],
                    keep_node_id=disk["node_id"],
                    keep_root_id=disk["root_id"],
                    download_name=landing["computer"],
                )
            return choice

        def with_downloads(disk, reason, waiting=False):
            """Add who downloads it when the kept disk can't."""
            if not acquire or disk["download"]:
                return picked(disk, reason)
            landing = downloader(disk)
            if landing:
                then = "moves it there when it's back" if waiting else "then moves it there"
                return picked(disk, f"{reason} {landing['computer']} downloads it and {then}.", landing)
            if waiting:
                return picked(disk, f"{reason} It's offline, so this starts when it's back.")
            return picked(disk, f"{reason} Nothing here can download yet, so this waits.")

        if single:
            disk = candidates[0]
            why = problem(disk)
            whose = {
                "request": "as chosen",
                "personal": "your storage",
                "server": "the household's storage",
            }.get(source, "as chosen")
            if not disk["online"] or why.endswith("is offline"):
                return with_downloads(disk, f"On {disk['name']}, {whose}.", waiting=True)
            if why:
                raise PlacementError(
                    f"{why}, and this needs about {plain_size(needed)}. Free some space or choose another place."
                    if "full" in why
                    else f"{why}. Choose another place."
                )
            return with_downloads(disk, f"On {disk['name']}, {whose}.")

        by_key = {d["key"]: d for d in candidates}
        notes = [f"Storage is off on {d['computer']}." for d in resting if d["key"] in self.home(tmdb_id, media_type)] if together else []
        for key in self.home(tmdb_id, media_type) if together else ():
            disk = by_key.get(key)
            if not disk:
                continue
            beside = "with the other copy" if film else "with the rest of the show"
            why = problem(disk)
            if not why:
                return with_downloads(disk, f"Kept {beside} on {disk['name']}.")
            if not disk["online"]:
                # Waiting keeps a show in one place; a new copy elsewhere splits it.
                return with_downloads(disk, f"Kept {beside} on {disk['name']}.", waiting=True)
            notes.append(why + ".")
            break
        eligible = [d for d in candidates if not problem(d)]
        if not eligible:
            reasons = " ".join(problem(d) + "." for d in candidates)
            raise PlacementError(f"No storage in {scope} can take this right now. {reasons}")
        if len(candidates) == 1:
            chosen, reason = eligible[0], f"On {eligible[0]['name']}."
        elif rule == "fill":
            chosen = eligible[0]
            skipped = [problem(d) for d in candidates[: candidates.index(chosen)]]
            notes += [s + "." for s in skipped if s]
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
            notes += [s + "." for s in skipped if s]
            if commit:
                self._update(pool_id, cursor=candidates.index(chosen))
            reason = f"On {chosen['name']}, next in turn."
        else:
            chosen = max(eligible, key=lambda d: d["free_bytes"] or 0)
            reason = f"On {chosen['name']}, which has the most free space."
            notes += [
                problem(d) + "."
                for d in candidates
                if problem(d) and d["computer"] != chosen["computer"]
            ]
        choice = with_downloads(chosen, reason)
        notes = list(dict.fromkeys(notes))
        if notes:
            choice["reason"] = choice["reason"] + " " + " ".join(notes)
        return choice

    def landing(self, user, node_id, root_id, *, tmdb_id, media_type, count=1, values=None):
        """For work pinned to one computer: download there, or elsewhere and move it there."""
        disk = next(
            (d for d in self.disks() if d["node_id"] == (node_id or "local") and d["root_id"] == (root_id or "library")),
            None,
        )
        if not disk or disk["download"]:
            return None
        try:
            choice = self.choose(
                user,
                f"{disk['node_id']}/{disk['root_id']}",
                tmdb_id=tmdb_id,
                media_type=media_type,
                count=count,
                values=values,
                together=False,
            )
        except PlacementError:
            return None
        return choice if choice.get("keep_node_id") else None

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
