"""Found: titles already in a computer's library folders that Sparrow doesn't know.

Sparrow looks in every library folder by itself: when one is chosen, when a
computer is first seen, every few hours, and when someone asks it to look
again. A file counts once it has been seen unchanged on two looks at least ten
minutes apart, so a copy still arriving or a download being put away is left
alone.

Each file is then named from facts: its name and folders, TMDB, and the length
ffprobe measures. Sparrow adds a title by itself only when they agree: the name
(and year, when the file has one) matches exactly one film and its length is
within a few minutes, or the name matches exactly one series and that episode
exists. Anything else waits for someone to say what it is. Files are never
moved or renamed. Titles someone names get subtitles prepared as their
preferences say, like a download; those Sparrow adds by itself don't, so a
whole NAS doesn't queue hours of listening.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import computers
from .account_api import administrator
from .node_executor import NodeError, canonical, is_library
from .tools import ToolError

logger = logging.getLogger(__name__)
# A file must look the same this long apart before it's named.
SETTLE = float(os.getenv("SPARROW_FOUND_SETTLE", "600"))
EVERY = 6 * 3600.0  # How often each library folder is looked in anyway.
TICK = float(os.getenv("SPARROW_FOUND_TICK", "60"))
ACTIVE = ("copying", "switching", "cleaning")
TABLES = (
    "CREATE TABLE IF NOT EXISTS found_files(node_id TEXT NOT NULL, root_id TEXT NOT NULL, "
    "path TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(node_id, root_id, path))",
    "CREATE TABLE IF NOT EXISTS found_looks(node_id TEXT PRIMARY KEY, data TEXT NOT NULL)",
)

# ─── Guessing a title from where a file is ───────────────────────────

EXTRAS = re.compile(
    r"(^|[\W_])(sample|trailers?|featurettes?|behind[ ._-]the[ ._-]scenes|deleted[ ._-]scenes|"
    r"extras|bonus|interviews?|specials?[ ._-]features?)([\W_]|$)",
    re.I,
)
SEASON_FOLDER = re.compile(r"^(?:season|series|s)[ ._-]*(\d{1,2})$", re.I)
EPISODE_ONLY = re.compile(r"^(?:e|ep|episode)?[ ._-]*(\d{1,3})(?:\D|$)", re.I)
YEAR = re.compile(r"(?<![0-9])((?:19|20)\d{2})(?![0-9])")
EPISODE_MARK = re.compile(r"(?i)(?<![a-z0-9])s?\d{1,2}[ex]\d{1,3}")
TAGS = re.compile(
    r"[ ._-](?:2160p|1080p|720p|480p|4k|uhd|hdr|bluray|blu-ray|bdrip|brrip|web-?dl|webrip|web|"
    r"hdtv|dvdrip|remux|x26[45]|h\.?26[45]|hevc|av1|aac|ac3|dts|proper|repack|extended|"
    r"unrated|directors[ ._-]cut)(?:[ ._-]|$).*$",
    re.I,
)


def clean(text: str) -> str:
    """A title as written: separators to spaces, brackets and leftovers gone."""
    text = TAGS.sub("", text)
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"[\[\](){}]", " ", text)
    text = re.sub(r"\s+-\s*$|^\s*-\s+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -")


def norm(text: str) -> str:
    """A title for comparing: case, punctuation and a leading "The" don't count."""
    text = (text or "").casefold().replace("&", " and ")
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^\w]+", " ", text).strip()
    return re.sub(r"^the ", "", text)


def _title_year(name: str) -> tuple[str, int | None]:
    """'Blade.Runner.2049.2017.1080p' → ('Blade Runner 2049', 2017): the last year counts."""
    name = TAGS.sub("", name)
    years = list(YEAR.finditer(name))
    for found in reversed(years):
        before = clean(name[: found.start()])
        if before:
            return before, int(found.group(1))
    return clean(name), None


def guess(path: str) -> dict | None:
    """What a file probably is, from its name and folders. None: an extra, not a title."""
    from ..services.release_parser import parse_release_name

    parts = PurePosixPath(path).parts
    if any(EXTRAS.search(p) for p in parts):
        return None
    stem = PurePosixPath(parts[-1]).stem
    folders = list(parts[:-1])
    parsed = parse_release_name(stem)
    season_at = next((n for n, f in enumerate(folders) if SEASON_FOLDER.match(f)), None)
    episodes = [list(e) for e in parsed.episodes]
    if not episodes and season_at is not None:
        number = EPISODE_ONLY.match(stem)
        if number:
            episodes = [[int(SEASON_FOLDER.match(folders[season_at]).group(1)), int(number.group(1))]]
    if episodes:
        season, episode = episodes[0]
        # Show.Name.2019.S01E02.mkv: the show is whatever comes before the numbers.
        numbers = EPISODE_MARK.search(stem)
        title, year = _title_year(stem[: numbers.start()]) if numbers else ("", None)
        if not title:
            # Show/Season 01/01 - Pilot.mkv: the show is the folder above the season.
            show = folders[season_at - 1] if season_at else (folders[-1] if folders else "")
            title, year = _title_year(show)
        return {"media_type": "tv", "title": title, "year": year, "season": season, "episode": episode}
    title, year = _title_year(stem)
    if (not year or not title) and folders:
        # Films/Arrival (2016)/Arrival.mkv: the folder knows the year.
        folder_title, folder_year = _title_year(folders[-1])
        if folder_year and (not title or norm(folder_title).startswith(norm(title)) or norm(title).startswith(norm(folder_title))):
            title, year = folder_title, folder_year
    return {"media_type": "movie", "title": title, "year": year, "season": None, "episode": None}


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


class Unsure(Exception):
    """Why Sparrow won't add a file by itself, in the household's words."""


def _tables(db):
    for table in TABLES:
        db.execute(table)


def summary(accounts, node_id) -> dict | None:
    """What a computer's card says about Found: when it last looked, what it added
    this week, and how many files wait for a name."""
    try:
        with accounts.connect() as db:
            _tables(db)
            look = db.execute("SELECT data FROM found_looks WHERE node_id=?", (node_id,)).fetchone()
            rows = db.execute("SELECT data FROM found_files WHERE node_id=?", (node_id,)).fetchall()
    except Exception:  # No accounts database yet.
        return None
    look = json.loads(look["data"]) if look else {}
    week = time.time() - 7 * 86400
    unsure = added = 0
    for row in rows:
        data = json.loads(row["data"])
        if data.get("state") == "unsure":
            unsure += 1
        elif data.get("state") == "added" and (data.get("added") or {}).get("at", 0) > week:
            added += 1
    return {
        "checked": look.get("checked", 0),
        "looking": bool(look.get("looking")) or look.get("requested", 0) > look.get("checked", 0),
        "unsure": unsure,
        "added": added,
        "error": look.get("error", ""),
    }


class Found:
    def __init__(self, accounts, nodes, storage, catalogue, get_service):
        self.accounts, self.nodes, self.storage = accounts, nodes, storage
        self.catalogue, self.get_service = catalogue, get_service
        self.wake = asyncio.Event()
        self._looking = set()
        with self.accounts.connect() as db:
            _tables(db)
            # A look cut short by a restart isn't still going.
            db.execute("UPDATE found_looks SET data=json_set(data, '$.looking', json('false'))")

    # ─── What a computer's card shows ────────────────────────────────

    def _look(self, node_id) -> dict:
        with self.accounts.connect() as db:
            row = db.execute("SELECT data FROM found_looks WHERE node_id=?", (node_id,)).fetchone()
        return json.loads(row["data"]) if row else {}

    def _save_look(self, node_id, **changes):
        data = {**self._look(node_id), **changes}
        with self.accounts.connect() as db:
            db.execute(
                "INSERT INTO found_looks VALUES (?, ?) ON CONFLICT(node_id) DO UPDATE SET data=excluded.data",
                (node_id, json.dumps(data)),
            )
        return data

    def summary(self, node_id) -> dict:
        return summary(self.accounts, node_id)

    def files(self, node_id) -> dict:
        """The files waiting for a name, and what was added lately."""
        unsure, added = [], []
        with self.accounts.connect() as db:
            rows = db.execute(
                "SELECT root_id, path, data FROM found_files WHERE node_id=? ORDER BY path", (node_id,)
            ).fetchall()
        for row in rows:
            data = json.loads(row["data"])
            entry = {
                "root_id": row["root_id"],
                "path": row["path"],
                "name": PurePosixPath(row["path"]).name,
                "size_bytes": data.get("size_bytes", 0),
            }
            if data.get("state") == "unsure":
                unsure.append({**entry, "guess": data.get("guess"), "reason": data.get("reason", "")})
            elif data.get("state") == "added":
                added.append({**entry, **(data.get("added") or {})})
        added.sort(key=lambda a: -a.get("at", 0))
        return {"unsure": unsure, "added": added[:50]}

    def request(self, node_id):
        """Look again soon, because someone asked or a folder changed."""
        self._save_look(node_id, requested=time.time())
        self.wake.set()

    # ─── Looking ─────────────────────────────────────────────────────

    def _roots(self, node) -> list[str]:
        from .relocation import retiring

        leaving = retiring(self.accounts)
        return [
            r["id"]
            for r in node["capabilities"].get("roots") or []
            if isinstance(r, dict)
            and is_library(r.get("id"))
            and r.get("available")
            and (node["id"], r["id"]) not in leaving
        ]

    def _due(self, node, now) -> bool:
        look = self._look(node["id"])
        checked = look.get("checked", 0)
        if not checked or look.get("requested", 0) > checked or now - checked > EVERY:
            return True
        with self.accounts.connect() as db:
            rows = db.execute(
                "SELECT data FROM found_files WHERE node_id=?", (node["id"],)
            ).fetchall()
        # A new file is named on the first look after it has had time to settle.
        return any(
            json.loads(r["data"]).get("state") == "new"
            and json.loads(r["data"]).get("first", now) + SETTLE <= now
            and checked < json.loads(r["data"]).get("first", now) + SETTLE
            for r in rows
        )

    async def run_pending(self, wait=True):
        """Look in every computer that's due, each on its own so a slow one holds up none."""
        now = time.time()
        started = []
        for node in self.nodes.list():
            if node["disabled"] or not node["online"] or node["id"] in self._looking:
                continue
            jobs = computers.wanted(self.accounts, node["id"], node["capabilities"])
            if not jobs.get("storage") or not self._roots(node) or not self._due(node, now):
                continue
            self._looking.add(node["id"])
            started.append(asyncio.ensure_future(self._look_safely(node)))
        if wait and started:
            await asyncio.gather(*started)

    async def _look_safely(self, node):
        try:
            await self.look(node)
        except Exception:  # One computer never stops the others.
            logger.exception("looking in %s's folders failed", node["name"])

    async def loop(self):
        while True:
            self.wake.clear()
            await self.run_pending(wait=False)
            try:
                await asyncio.wait_for(self.wake.wait(), TICK)
            except asyncio.TimeoutError:
                pass

    def _known(self, node_id) -> set[tuple[str, str]]:
        """Files the collection already has, or that a move is putting in place."""
        with self.accounts.connect() as db:
            known = {
                (r["root_id"], r["path"])
                for r in db.execute("SELECT root_id, path FROM assets WHERE node_id=?", (node_id,))
            }
            moves = db.execute(
                f"SELECT data FROM storage_moves WHERE state IN ({','.join('?' * len(ACTIVE))})", ACTIVE
            ).fetchall() if self._has("storage_moves", db) else []
        for row in moves:
            data = json.loads(row["data"])
            if data["to_node"] == node_id:
                known |= {(data["to_root"], f["path"]) for a in data["assets"] for f in a["files"]}
        return known

    @staticmethod
    def _has(table, db) -> bool:
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())

    async def look(self, node):
        node_id = node["id"]
        started = time.time()
        self._looking.add(node_id)
        self._save_look(node_id, looking=True)
        error = ""
        try:
            seen, looked = {}, set()
            for root_id in self._roots(node):
                try:
                    listed = await self.nodes.execute(node_id, "list", {"root_id": root_id}, timeout=600)
                except NodeError as exc:
                    error = str(exc)
                    continue
                looked.add(root_id)
                for entry in listed:
                    seen[(root_id, entry["path"])] = entry
            known = self._known(node_id)
            now = time.time()
            with self.accounts.connect() as db:
                saved = {
                    (r["root_id"], r["path"]): json.loads(r["data"])
                    for r in db.execute(
                        "SELECT root_id, path, data FROM found_files WHERE node_id=?", (node_id,)
                    )
                }
                for key, data in saved.items():
                    # Gone, or in the collection some other way: forget it. What Found
                    # added stays as a record while its file is there.
                    # A folder that couldn't be listed keeps what it had.
                    gone = key[0] in looked and key not in seen
                    collected = key in known and data.get("state") != "added"
                    if gone or collected:
                        db.execute(
                            "DELETE FROM found_files WHERE node_id=? AND root_id=? AND path=?",
                            (node_id, *key),
                        )
                fresh = set()  # New or changed on this look: named on a later one.
                for key, entry in seen.items():
                    if key in known:
                        continue
                    data = saved.get(key)
                    version = entry["version"]
                    if data and data.get("version") == version:
                        continue
                    fresh.add(key)
                    db.execute(
                        "INSERT OR REPLACE INTO found_files VALUES (?, ?, ?, ?)",
                        (
                            node_id,
                            *key,
                            json.dumps(
                                {"state": "new", "version": version, "size_bytes": entry["size_bytes"], "first": now}
                            ),
                        ),
                    )
            settled = []
            with self.accounts.connect() as db:
                for row in db.execute(
                    "SELECT root_id, path, data FROM found_files WHERE node_id=?", (node_id,)
                ):
                    data = json.loads(row["data"])
                    key = (row["root_id"], row["path"])
                    if data.get("state") == "new" and key not in fresh and data["first"] + SETTLE <= now:
                        settled.append((row["root_id"], row["path"], data))
            if settled:
                error = await self.name(node_id, settled) or error
        finally:
            self._looking.discard(node_id)
            self._save_look(node_id, checked=started, error=error, looking=False)

    # ─── Naming ──────────────────────────────────────────────────────

    async def _tmdb(self, path, **params):
        service = self.get_service()
        if not service:
            raise ToolError("Sparrow is still starting.")
        return await service.toolbox.tmdb_get(path, **params)

    async def _details(self, media_type, tmdb_id):
        cached = self.catalogue.cached_title(media_type, tmdb_id)
        if cached:
            return cached
        details = await self._tmdb(f"/{media_type}/{tmdb_id}")
        return self.catalogue.cache_title(media_type, tmdb_id, details)

    async def _season(self, cache, tmdb_id, season):
        key = (tmdb_id, season)
        if key not in cache:
            cache[key] = await self._tmdb(f"/tv/{tmdb_id}/season/{season}")
        return cache[key]

    async def _search(self, cache, guessed):
        kind = guessed["media_type"]
        key = (kind, norm(guessed["title"]), guessed.get("year"))
        if key not in cache:
            params = {"query": guessed["title"], "include_adult": "false"}
            if guessed.get("year"):
                params["year" if kind == "movie" else "first_air_date_year"] = guessed["year"]
            cache[key] = (await self._tmdb(f"/search/{kind}", **params)).get("results") or []
        return cache[key]

    async def match(self, guessed, minutes, cache) -> dict:
        """The one title these facts agree on, or Unsure with why not."""
        kind, title, year = guessed["media_type"], guessed["title"], guessed.get("year")
        if not title:
            raise Unsure("The file name doesn't say what it is.")
        named = "film" if kind == "movie" else "series"
        results = await self._search(cache, guessed)
        same = [
            r
            for r in results
            if norm(title) in (norm(r.get("title") or r.get("name")), norm(r.get("original_title") or r.get("original_name")))
        ]
        if year:
            dated = "release_date" if kind == "movie" else "first_air_date"
            close = [r for r in same if _year(r.get(dated)) in (year - 1, year, year + 1)]
            exact = [r for r in close if _year(r.get(dated)) == year]
            same = exact if len(exact) == 1 else close
        if not same:
            raise Unsure(f"No {named} is called {title}{f' ({year})' if year else ''}.")
        if len(same) > 1:
            raise Unsure(f"More than one {named} is called {title}.")
        tmdb_id = int(same[0]["id"])
        if kind == "movie":
            details = await self._details("movie", tmdb_id)
            runtime = details.get("runtime") or 0
            if not runtime:
                raise Unsure(f"TMDB doesn't know how long {details.get('title', title)} is.")
            if abs(minutes - runtime) > max(8.0, runtime * 0.1):
                raise Unsure(f"It's {round(minutes)} minutes, and {details.get('title', title)} is {runtime}.")
            return {"tmdb_id": tmdb_id, "media_type": "movie", "title": details.get("title") or title,
                    "season": None, "episode": None}
        season, episode = guessed["season"], guessed["episode"]
        facts = await self._season(cache, tmdb_id, season)
        found = next((e for e in facts.get("episodes", []) if e.get("episode_number") == episode), None)
        if not found:
            raise Unsure(f"{same[0].get('name', title)} has no season {season}, episode {episode}.")
        runtime = found.get("runtime") or 0
        if runtime and abs(minutes - runtime) > max(6.0, runtime * 0.3):
            raise Unsure(f"It's {round(minutes)} minutes, and that episode is {runtime}.")
        if not runtime and minutes < 8:
            raise Unsure("It's too short to be an episode.")
        return {"tmdb_id": tmdb_id, "media_type": "tv", "title": same[0].get("name") or title,
                "season": season, "episode": episode}

    async def name(self, node_id, settled) -> str:
        """Add what the facts agree on; the rest waits for a person. Returns a problem, if any."""
        cache, sure = {}, []
        for root_id, path, data in settled:
            guessed = guess(path)
            if guessed is None:
                self._set(node_id, root_id, path, {**data, "state": "ignored", "reason": "An extra, not a title."})
                continue
            try:
                facts = await self.nodes.execute(
                    node_id, "probe", {"root_id": root_id, "path": path}, timeout=120
                )
                minutes = float(facts.get("duration") or 0) / 60
                if minutes < 1:
                    raise Unsure("It's under a minute long.")
                matched = await self.match(guessed, minutes, cache)
                sure.append((root_id, path, data, matched))
            except Unsure as why:
                self._set(node_id, root_id, path, {**data, "state": "unsure", "guess": guessed, "reason": str(why)})
            except NodeError:
                self._set(node_id, root_id, path, {**data, "state": "unsure", "guess": guessed, "reason": "Sparrow couldn't read this file."})
            except ToolError:
                # Nothing more can be named until TMDB answers. They stay new for next time.
                return "TMDB didn't answer, so nothing new was named. Sparrow tries again later."
        if sure:
            await self.add(node_id, [
                {"root_id": r, "path": p, **m} for r, p, _, m in sure
            ], automatic=True)
        return ""

    def _set(self, node_id, root_id, path, data):
        with self.accounts.connect() as db:
            db.execute(
                "UPDATE found_files SET data=? WHERE node_id=? AND root_id=? AND path=?",
                (json.dumps(data), node_id, root_id, path),
            )

    async def add(self, node_id, files, automatic=False) -> dict:
        """Add files to the collection as these titles. Files stay where they are."""
        added, failed = [], []
        by_root = {}
        for f in files:
            by_root.setdefault(f["root_id"], []).append(f)
        with self.accounts.connect() as db:
            rows = {
                (r["root_id"], r["path"]): json.loads(r["data"])
                for r in db.execute("SELECT root_id, path, data FROM found_files WHERE node_id=?", (node_id,))
            }
        for root_id, group in by_root.items():
            candidates = []
            for f in group:
                data = rows.get((root_id, f["path"]))
                if not data:
                    failed.append({"path": f["path"], "message": "Sparrow no longer sees this file. Look again."})
                    continue
                candidates.append(
                    {
                        "id": hashlib.sha256(f["path"].encode()).hexdigest()[:24],
                        "path": f["path"],
                        "root_id": root_id,
                        "name": PurePosixPath(f["path"]).name,
                        "version": data["version"],
                        "size_bytes": data.get("size_bytes", 0),
                    }
                )
            if not candidates:
                continue
            scan_id = secrets.token_hex(16)
            with self.accounts.connect() as db:
                db.execute(
                    "INSERT INTO import_scans VALUES (?, ?, ?, ?, ?)",
                    (scan_id, node_id, root_id, canonical(candidates), time.time()),
                )
            ids = {c["path"]: c["id"] for c in candidates}
            selections = [
                {
                    "id": ids[f["path"]],
                    "title": f.get("title", ""),
                    "tmdb_id": f["tmdb_id"],
                    "media_type": f["media_type"],
                    "season": f.get("season"),
                    "episode": f.get("episode"),
                }
                for f in group
                if f["path"] in ids
            ]
            result = await self.catalogue.confirm_import(
                scan_id, selections, self._details, lambda tmdb_id, season: self._season({}, tmdb_id, season)
            )
            paths = {c["id"]: c["path"] for c in candidates}
            for imported in result["imported"]:
                path = paths[imported["id"]]
                data = rows[(root_id, path)]
                self._set(node_id, root_id, path, {
                    **data, "state": "added",
                    "added": {"title": imported["title"], "item_id": imported["item_id"],
                              "at": time.time(), "automatic": automatic},
                })
                added.append(
                    {"path": path, "title": imported["title"], "item_id": imported["item_id"],
                     "asset_id": imported["asset_id"]}
                )
            for problem in result["failed"]:
                path = paths[problem["id"]]
                data = rows[(root_id, path)]
                self._set(node_id, root_id, path, {
                    **data, "state": "unsure", "guess": data.get("guess") or guess(path),
                    "reason": problem["message"],
                })
                failed.append({"path": path, "message": problem["message"]})
        return {"added": added, "failed": failed}

    def ignore(self, node_id, files):
        """Not a title: stop asking about it while it stays the same file."""
        with self.accounts.connect() as db:
            for f in files:
                row = db.execute(
                    "SELECT data FROM found_files WHERE node_id=? AND root_id=? AND path=?",
                    (node_id, f["root_id"], f["path"]),
                ).fetchone()
                if row:
                    data = {**json.loads(row["data"]), "state": "ignored", "reason": "Not a title."}
                    db.execute(
                        "UPDATE found_files SET data=? WHERE node_id=? AND root_id=? AND path=?",
                        (json.dumps(data), node_id, f["root_id"], f["path"]),
                    )


def install_found(app, accounts, nodes, storage, catalogue, get_service):
    found = Found(accounts, nodes, storage, catalogue, get_service)
    router = APIRouter(prefix="/api/v1/admin/found")

    class File(BaseModel):
        model_config = ConfigDict(extra="forbid")
        root_id: str = Field(pattern=r"^library(-\d{1,2})?$")
        path: str = Field(min_length=1, max_length=2000)

    class Named(File):
        tmdb_id: int = Field(gt=0)
        media_type: Literal["movie", "tv"]
        season: int | None = Field(default=None, ge=0)
        episode: int | None = Field(default=None, ge=1)

    class Adding(BaseModel):
        model_config = ConfigDict(extra="forbid")
        files: list[Named] = Field(min_length=1, max_length=200)

    class Ignoring(BaseModel):
        model_config = ConfigDict(extra="forbid")
        files: list[File] = Field(min_length=1, max_length=500)

    def paired(node_id):
        node = nodes.info(node_id)
        if not node or node["disabled"]:
            raise HTTPException(404, "This computer isn’t paired.")
        return node

    @router.get("/{node_id}")
    def listing(node_id: str, request: Request):
        administrator(request)
        paired(node_id)
        return {**found.files(node_id), "summary": found.summary(node_id)}

    @router.post("/{node_id}/look")
    def look(node_id: str, request: Request):
        """Look in a computer's folders now."""
        administrator(request)
        node = paired(node_id)
        if not node["online"]:
            raise HTTPException(409, f"{node['name']} is offline. Wake it, then try again.")
        found.request(node_id)
        return found.summary(node_id)

    @router.post("/{node_id}/add")
    async def add(node_id: str, body: Adding, request: Request):
        """Add files as the titles someone named."""
        administrator(request)
        paired(node_id)
        for f in body.files:
            if f.media_type == "tv" and (f.season is None or f.episode is None):
                raise HTTPException(422, "Choose the season and episode for each episode.")
        try:
            result = await found.add(node_id, [f.model_dump() for f in body.files])
        except (NodeError, ToolError) as exc:
            raise HTTPException(422, str(exc)) from exc
        # Named by a person: subtitles as their preferences say, as for a download.
        subtitles = getattr(request.app.state, "subtitles", None)
        prefs = accounts.resolve(request.state.user["id"])
        values = prefs["values"]
        if subtitles and values.get("subtitle_mode") != "off" and (
            values.get("subtitle_auto_prepare", True) or values.get("require_subtitles", False)
        ):
            for added in result["added"]:
                try:
                    await subtitles.enqueue(request.state.user, added["asset_id"], preferences=prefs)
                except (ToolError, ValueError):
                    pass  # The title plays; its Subtitles button tries again.
        return result

    @router.post("/{node_id}/ignore")
    def ignore(node_id: str, body: Ignoring, request: Request):
        administrator(request)
        paired(node_id)
        found.ignore(node_id, [f.model_dump() for f in body.files])
        return {**found.files(node_id), "summary": found.summary(node_id)}

    app.include_router(router)
    return found
