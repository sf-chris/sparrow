"""Asset identities, import receipts, cached title facts and personal watch state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path

from .accounts import Accounts
from .runtime import ToolError
from .node_executor import canonical, NodeError
from .media_state import media_state
from ..models import LibraryItem, MediaType


class Catalogue:
    def __init__(self, storage, nodes):
        self.storage, self.nodes = storage, nodes
        self.accounts = Accounts(storage.data_dir)
        with self.accounts.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, item_id TEXT NOT NULL,
                    node_id TEXT NOT NULL, root_id TEXT NOT NULL, path TEXT NOT NULL, data TEXT NOT NULL,
                    UNIQUE(node_id,root_id,path));
                CREATE TABLE IF NOT EXISTS title_cache(media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL,
                    data TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(media_type,tmdb_id));
                CREATE TABLE IF NOT EXISTS import_scans(id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
                    root_id TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS watch_state(user_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                    position REAL NOT NULL, duration REAL NOT NULL, watched INTEGER NOT NULL,
                    updated REAL NOT NULL, session_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    PRIMARY KEY(user_id,asset_id));
            """)

    def visible_item(self, user, item):
        return self.accounts.can_access(user, item.metadata.get("library_id", "local"))

    def item(self, user, item_id):
        item = self.storage.get_library_item(item_id)
        return item if item and self.visible_item(user, item) else None

    def assets(self, user=None, item_id=None, asset_id=None):
        query, args = "SELECT * FROM assets", []
        if asset_id:
            query += " WHERE id=?"
            args.append(asset_id)
        elif item_id:
            query += " WHERE item_id=?"
            args.append(item_id)
        with self.accounts.connect() as db:
            rows = db.execute(query, args).fetchall()
            watches = (
                {
                    r["asset_id"]: dict(r)
                    for r in db.execute(
                        "SELECT asset_id,position,duration,watched,updated FROM watch_state WHERE user_id=?",
                        (user["id"],),
                    )
                }
                if user
                else {}
            )
        node_info = (
            {n["id"]: n for n in self.nodes.list()}
            if any(r["node_id"] != "local" for r in rows)
            else {}
        )
        out = []
        for row in rows:
            asset = {**dict(row), **json.loads(row["data"])}
            asset.pop("data", None)
            if user and not self.accounts.can_access(user, asset["node_id"]):
                continue
            if asset["node_id"] == "local":
                try:
                    path = self.nodes.local().path(asset["root_id"], asset["path"])
                    state = media_state(
                        {"verified": True, "file_version": asset["facts"]["version"]},
                        str(path),
                    )
                except (NodeError, OSError):
                    state = "unavailable"
            else:
                node = node_info.get(asset["node_id"])
                root = next(
                    (
                        r
                        for r in (node or {}).get("capabilities", {}).get("roots", [])
                        if r["id"] == asset["root_id"]
                    ),
                    {},
                )
                state = (
                    "ready"
                    if node and node["online"] and root.get("available")
                    else "unavailable"
                )
            asset["state"] = state
            if user:
                asset["watch"] = watches.get(asset["id"])
            out.append(asset)
        return out

    def asset(self, user, asset_id):
        return next(iter(self.assets(user, asset_id=asset_id)), None)

    def save_asset(
        self, item_id, node_id, root_id, path, facts, *, season=None, episode=None
    ):
        identity = hashlib.sha256(f"{node_id}\0{root_id}\0{path}".encode()).hexdigest()[
            :32
        ]
        with self.accounts.connect() as db:
            existing = db.execute(
                "SELECT item_id,data FROM assets WHERE id=?", (identity,)
            ).fetchone()
            data = json.loads(existing["data"]) if existing else {}
            if existing and existing["item_id"] != item_id:
                # A corrected match changes the title mapping; the file identity and personal progress survive.
                data["previous_item_id"] = existing["item_id"]
            data.update(
                {
                    "facts": facts,
                    "season": season,
                    "episode": episode,
                    "verified_at": time.time(),
                }
            )
            db.execute(
                "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "item_id=excluded.item_id,data=excluded.data",
                (identity, item_id, node_id, root_id, path, canonical(data)),
            )
        return identity

    async def migrate_verified_legacy(self, broadcast=None):
        """Re-probe confirmed legacy records inside the approved local library.

        Filename-only guesses and unavailable volumes remain unverified. Nothing
        is renamed or removed, and existing title identity is preserved.
        """
        from .media_state import file_version

        migrated = 0
        for item in self.storage.get_library():
            records = (
                [(None, None, item.metadata)]
                if item.media_type.value == "movie"
                else [
                    (int(s), int(e), r)
                    for s, eps in item.episodes.items()
                    for e, r in eps.items()
                ]
            )
            changed = False
            for season, episode, record in records:
                if (
                    record.get("asset_id")
                    or not record.get("verified")
                    or record.get("node_id")
                ):
                    continue
                path = record.get("path") or (
                    item.path if item.media_type.value == "movie" else ""
                )
                try:
                    root = self.nodes.local().roots.get("library")
                    if not root or not path:
                        continue
                    relative = Path(path).resolve().relative_to(root).as_posix()
                    facts = await self.nodes.execute(
                        "local",
                        "probe",
                        {"root_id": "library", "path": relative},
                        timeout=60,
                    )
                    record.update(
                        {
                            "asset_id": self.save_asset(
                                item.id,
                                "local",
                                "library",
                                relative,
                                facts,
                                season=season,
                                episode=episode,
                            ),
                            "file_version": facts["version"],
                            "quality": facts["quality"],
                            "audio_languages": facts["audio_languages"],
                            "audio_tracks": facts["audio_tracks"],
                        }
                    )
                    changed = True
                    migrated += 1
                except (NodeError, OSError, ValueError):
                    continue
            if changed:
                await self.storage.add_library_item(item)
                if broadcast:
                    await broadcast({"type": "library_update", "data": {"id": item.id}})
        return {"migrated": migrated}

    def cached_title(self, media_type, tmdb_id):
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT data FROM title_cache WHERE media_type=? AND tmdb_id=?",
                (media_type, tmdb_id),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def cache_title(self, media_type, tmdb_id, data):
        with self.accounts.connect() as db:
            db.execute(
                "INSERT INTO title_cache VALUES (?, ?, ?, ?) ON CONFLICT(media_type,tmdb_id) "
                "DO UPDATE SET data=excluded.data,updated=excluded.updated",
                (media_type, tmdb_id, canonical(data), time.time()),
            )
        return data

    async def scan(self, node_id, root_id="library", path=""):
        files = await self.nodes.execute(
            node_id, "list", {"root_id": root_id, "path": path}, timeout=90
        )
        identity = secrets.token_hex(16)
        from ..services.release_parser import parse_release_name

        candidates = []
        for entry in files:
            parsed = parse_release_name(Path(entry["path"]).name)
            episodes = list(parsed.episodes)
            candidates.append(
                {
                    **entry,
                    "id": hashlib.sha256(entry["path"].encode()).hexdigest()[:24],
                    "suggested_title": parsed.title or Path(entry["path"]).stem,
                    "media_type": "tv" if episodes else "movie",
                    "season": episodes[0][0] if episodes else None,
                    "episode": episodes[0][1] if episodes else None,
                }
            )
        with self.accounts.connect() as db:
            db.execute(
                "INSERT INTO import_scans VALUES (?, ?, ?, ?, ?)",
                (identity, node_id, root_id, canonical(candidates), time.time()),
            )
        return {
            "id": identity,
            "node_id": node_id,
            "root_id": root_id,
            "candidates": candidates,
        }

    async def confirm_import(self, scan_id, selections, get_title, get_episode=None):
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT * FROM import_scans WHERE id=?", (scan_id,)
            ).fetchone()
        if not row:
            raise NodeError(
                "This import preview no longer exists. Scan the folder again."
            )
        candidates = {c["id"]: c for c in json.loads(row["data"])}
        results = []
        failed = []
        for selection in selections:
            try:
                results.append(
                    await self._import_one(
                        row, candidates, selection, get_title, get_episode
                    )
                )
            except (NodeError, ValueError, ToolError) as exc:
                failed.append({"id": selection["id"], "message": str(exc)})
        return {"imported": results, "failed": failed}

    async def _import_one(self, row, candidates, selection, get_title, get_episode):
        candidate = candidates.get(selection["id"])
        if not candidate:
            raise NodeError("Select a file from this preview.")
        media_type, tmdb_id = selection["media_type"], selection.get("tmdb_id")
        if media_type not in ("movie", "tv"):
            raise NodeError("Choose movie or TV show.")
        details = await get_title(media_type, tmdb_id) if tmdb_id else {}
        title = (
            details.get("title") or details.get("name") or selection.get("title") or ""
        ).strip()
        if not title:
            raise NodeError("Confirm a title for each selected file.")
        season, episode = selection.get("season"), selection.get("episode")
        if media_type == "tv" and (
            not isinstance(season, int)
            or season < 0
            or not isinstance(episode, int)
            or episode < 1
        ):
            raise NodeError("Confirm the season and episode for TV files.")
        if media_type == "tv" and tmdb_id and get_episode:
            episode_facts = await get_episode(tmdb_id, season)
            if episode not in {
                e["episode_number"] for e in episode_facts.get("episodes", [])
            }:
                raise NodeError(
                    "This episode does not exist in the selected show and season."
                )
        facts = await self.nodes.execute(
            row["node_id"],
            "probe",
            {"root_id": row["root_id"], "path": candidate["path"]},
            timeout=90,
        )
        if facts["version"] != candidate["version"]:
            raise NodeError(
                "A file changed since the preview. Scan it again before importing."
            )
        # Matched titles merge only within a single library. Unmatched files
        # remain distinct, even when they have the same provisional title.
        key = f"{row['node_id']}:{media_type}:{tmdb_id or candidate['id']}"
        item_id = hashlib.sha256(key.encode()).hexdigest()[:24]
        item = self.storage.get_library_item(item_id)
        year_raw = (details.get("release_date") or details.get("first_air_date") or "")[
            :4
        ]
        if not item:
            item = LibraryItem(
                id=item_id,
                title=title,
                media_type=MediaType(media_type),
                path="",
                tmdb_id=tmdb_id,
                overview=details.get("overview", ""),
                year=int(year_raw) if year_raw.isdigit() else None,
                poster_path=details.get("poster_path") or "",
                backdrop_path=details.get("backdrop_path") or "",
                metadata={
                    "library_id": row["node_id"],
                    "match_source": "owner_confirmation",
                },
            )
        with self.accounts.connect() as db:
            previous = db.execute(
                "SELECT * FROM assets WHERE node_id=? AND root_id=? AND path=?",
                (row["node_id"], row["root_id"], candidate["path"]),
            ).fetchone()
        asset_id = self.save_asset(
            item_id,
            row["node_id"],
            row["root_id"],
            candidate["path"],
            facts,
            season=season,
            episode=episode,
        )
        record = {
            "verified": True,
            "asset_id": asset_id,
            "file_version": facts["version"],
            "quality": facts["quality"],
            "audio_tracks": facts["audio_tracks"],
            "audio_languages": facts["audio_languages"],
            "duration_minutes": facts["duration"] / 60,
            "added_at": time.time(),
            "size_bytes": facts["size_bytes"],
        }
        if row["node_id"] == "local":
            record["path"] = str(
                self.nodes.local().path(row["root_id"], candidate["path"])
            )
        else:
            record.update(
                {
                    "node_id": row["node_id"],
                    "path": candidate["path"],
                    "available": True,
                }
            )
        if media_type == "movie":
            item.path = record["path"]
            item.metadata.update(record)
            item.size_bytes = facts["size_bytes"]
        else:
            item.set_episode_file(season, episode, record)
            item.size_bytes = sum(
                r.get("size_bytes", 0)
                for s in item.episodes.values()
                for r in s.values()
            )
        await self.storage.add_library_item(item)
        if previous:
            previous_item = self.storage.get_library_item(previous["item_id"])
            if previous_item:
                for season_key, episodes in list(previous_item.episodes.items()):
                    for episode_key, entry in list(episodes.items()):
                        if entry.get("asset_id") == asset_id and (
                            previous_item.id != item.id
                            or (int(season_key), int(episode_key)) != (season, episode)
                        ):
                            del episodes[episode_key]
                    if not episodes:
                        del previous_item.episodes[season_key]
                with self.accounts.connect() as db:
                    remaining = db.execute(
                        "SELECT COUNT(*) FROM assets WHERE item_id=?",
                        (previous_item.id,),
                    ).fetchone()[0]
                if not remaining:
                    await self.storage.delete_library_item(previous_item.id)
                else:
                    await self.storage.add_library_item(previous_item)
        return {
            "id": selection["id"],
            "item_id": item.id,
            "asset_id": asset_id,
            "title": title,
        }
