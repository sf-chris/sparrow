"""The search benchmark (issue #15): what finding a title costs, before and after a change.

offline  Replays recorded indexer and TMDB answers (cassettes) through the real
         scout with no model call. Reports whether the right copy ranks first,
         how many searches and peeks it took, and how often the cheap path
         would have to escalate (no usable row, or no right copy listed).
live     Runs real Fetch Agents (real models, the real indexer and TMDB) on an
         isolated state directory and stops each request once its download is
         chosen: nothing is downloaded and no torrent is added. Reports cost
         per title, median, p90 and escalation rate from the cost ledger, and
         can record what the indexer answered as cassettes for offline replay.
titles   Pins a title set from an earlier run's state directory.

    .venv/bin/python tests/evals/search_bench.py offline [--set anime] [--output FILE] [--compare FILE]
    SPARROW_RUN_LIVE_BENCH=1 .venv/bin/python tests/evals/search_bench.py live --set anime --max-dollars 5
    .venv/bin/python tests/evals/search_bench.py titles --from-state data/anime-batch-3 --name batch-3 --write

Offline numbers are a projection about the scout, not a measurement of model
spend; only a live run measures cost.
"""

import argparse
import asyncio
import contextlib
import copy
import hashlib
import json
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agents import ledger, scout  # noqa: E402
from backend.agents import tools as tool_module  # noqa: E402
from backend.agents.runtime import ToolError  # noqa: E402
from backend.agents.tools import Toolbox  # noqa: E402

HERE = Path(__file__).resolve().parent
TITLES = HERE / "search-titles.json"
CASSETTES = HERE / "cassettes" / "search"
RESULTS = HERE / "results"
ARTIFACTS = HERE / "artifacts"
VERSION = 1
# The code whose behaviour the offline replay measures.
SCOUT_SOURCES = ("backend/agents/scout.py", "backend/agents/release_match.py", "backend/services/release_parser.py")
NO_RESULTS = {"id": "0", "name": "No results returned", "info_hash": "0" * 40, "seeders": "0", "leechers": "0",
              "num_files": "0", "size": "0", "username": "", "added": "0", "category": "0"}


# ─── Title sets and cassettes ─────────────────────────────────────────────


def title_sets(path=TITLES) -> dict:
    """{set name: [title]}, each title {key, title, media_type, tmdb_id, year, episodes}."""
    data = json.loads(Path(path).read_text())
    return {name: [{**title, "set": name} for title in titles] for name, titles in data["sets"].items()}


def chosen_titles(sets, names=None) -> list:
    names = names or list(sets)
    unknown = [n for n in names if n not in sets]
    if unknown:
        raise SystemExit(f"No title set named {', '.join(unknown)}. Sets: {', '.join(sets)}.")
    return [title for name in names for title in sets[name]]


def _normal(query) -> str:
    return " ".join(str(query).lower().split())


def tmdb_key(path, params=None) -> str:
    params = {k: v for k, v in (params or {}).items() if k != "api_key"}
    return path + ("?" + urllib.parse.urlencode(sorted(params.items())) if params else "")


def _row(key, values) -> dict:
    """An indexer row as apibay answers: strings, and a stable fake hash for
    synthetic rows ([id, name, seeders, size GB, files])."""
    if isinstance(values, dict):
        return dict(values)
    ident, name, seeders, gigabytes, files = values
    return {"id": str(ident), "name": name, "info_hash": hashlib.sha1(f"{key}:{ident}".encode()).hexdigest(),
            "seeders": str(seeders), "leechers": "0", "num_files": str(files), "size": str(int(gigabytes * 1e9)),
            "username": "fixture", "added": "1700000000", "category": "208"}


class Cassette:
    """One title's recorded (or synthetic) indexer, listing and TMDB answers."""

    def __init__(self, entry, title=None, source=""):
        self.key = entry["key"]
        self.source = entry.get("source") or source
        self.title = title or {}
        rows = [_row(self.key, r) for r in entry.get("rows", [])]
        for series in entry.get("series", []):
            for offset, number in enumerate(range(series["from"], series["to"] + 1)):
                rows.append(_row(self.key, [str(series["first_id"] + offset), series["name"].format(n=number),
                                            series["seeders"], series["gb"], series.get("files", 1)]))
        self.index = {_normal(q): [_row(self.key, r) for r in answer] for q, answer in (entry.get("index") or {}).items()}
        # What the index holds for this title: answers searches nobody recorded.
        corpus = {r["id"]: r for r in rows}
        for answer in self.index.values():
            corpus.update({r["id"]: r for r in answer if r.get("id") != "0"})
        self.corpus = list(corpus.values())
        self.listings = {str(k): [(name, int(float(mb) * 1e6)) for name, mb in v] for k, v in (entry.get("listings") or {}).items()}
        self.correct = [str(c) for c in entry.get("correct") or []]
        self.expect = entry.get("expect", "")
        self.entry = entry
        self.tmdb = self._tmdb(entry) if self.title else {}

    def _tmdb(self, entry):
        title, kind = self.title, "tv" if self.title["media_type"] == "tv" else "movie"
        ident, name, date = title["tmdb_id"], "name" if kind == "tv" else "title", "first_air_date" if kind == "tv" else "release_date"
        mine = {"id": ident, name: title["title"], date: f"{title.get('year') or 2000}-01-01",
                "original_language": entry.get("language", "ja" if title.get("set") == "anime" else "en")}
        alternatives = [{"iso_3166_1": c, "title": t, "type": kind_} for c, t, kind_ in entry.get("alternatives", [])]
        answers = {
            f"/{kind}/{ident}": dict(mine),
            f"/{kind}/{ident}/alternative_titles": {"id": ident, "results" if kind == "tv" else "titles": alternatives},
        }
        namesakes = []
        for other in entry.get("namesakes", []):
            namesakes.append({"id": other["id"], name: other["name"], date: f"{other['year']}-01-01",
                              "original_language": other.get("language", "en")})
            for season, names in (other.get("episodes") or {}).items():
                answers[f"/tv/{other['id']}/season/{season}"] = {
                    "episodes": [{"episode_number": int(e), "name": n} for e, n in names.items()]}
        answers[tmdb_key(f"/search/{kind}", {"query": title["title"]})] = {"results": [mine, *namesakes]}
        names = entry.get("episode_names") or {}
        for season, episodes in (title.get("episodes") or {}).items():
            known = {int(e): n for e, n in (names.get(str(season)) or {}).items()}
            last = max([*episodes, *known, 1])
            answers[f"/tv/{ident}/season/{season}"] = {"episodes": [
                {"episode_number": e, "name": known.get(e, f"Episode {e}")} for e in range(1, last + 1)]}
        answers.update(entry.get("tmdb") or {})  # recorded answers win
        return answers


def load_cassettes(directory=CASSETTES, sets=None) -> dict:
    """{key: Cassette} from every cassette file below a directory: set files
    ({"titles": [...]}) and single-title recordings ({"key": ...})."""
    by_key = {t["key"]: t for titles in (sets or title_sets()).values() for t in titles}
    out = {}
    for path in sorted(Path(directory).rglob("*.json")):
        data = json.loads(path.read_text())
        for entry in data.get("titles") or ([data] if "key" in data else []):
            out[entry["key"]] = Cassette(entry, by_key.get(entry["key"], entry.get("job")), data.get("source", ""))
    return out


def _words(text):
    return re.findall(r"[a-z0-9]+", scout._fold(text).lower())


def simulate(rows, query) -> list:
    """The index's answer from what it holds: every word in the name (case and
    punctuation ignored), none of the -words, most seeded first, at most 100."""
    need, without = set(), set()
    for part in str(query).split():
        if part.startswith("-") and len(part) > 1:
            without.update(_words(part[1:]))
        else:
            need.update(_words(part))
    hits = [r for r in rows if need <= set(_words(r.get("name", ""))) and not without & set(_words(r.get("name", "")))]
    return sorted(hits, key=lambda r: -int(r.get("seeders") or 0))[:100] or [dict(NO_RESULTS)]


class Replay:
    """A toolbox whose indexer, pack listings and TMDB answer from cassettes.

    It searches through the real Toolbox.index_search (its cache and the
    ledger's search log); only the network call underneath is replaced."""

    index_search = Toolbox.index_search

    def __init__(self, cassettes):
        self.cassettes = list(cassettes)
        self._search_cache = {}
        self._search_scopes = {}
        self.simulated = []  # queries no recording answered: served from a corpus
        self.searched = []
        self.tmdb, self.listings, self.recorded, self.corpus = {}, {}, {}, []
        for cassette in self.cassettes:
            self.tmdb.update(cassette.tmdb)
            self.listings.update(cassette.listings)
            self.recorded.update(cassette.index)
            self.corpus += cassette.corpus

    async def rate_limit_search(self, wait=0):
        return 0.0

    async def tmdb_get(self, path, **params):
        key = tmdb_key(path, params)
        if key not in self.tmdb:
            raise ToolError(f"TMDB returned 404 for {path}")
        return copy.deepcopy(self.tmdb[key])

    async def query(self, q, cat="0", *, strict=False):
        """Stands in for the indexer query (search_engine._query), with its filtering."""
        self.searched.append(q)
        rows = self.recorded.get(_normal(q))
        if rows is None:
            self.simulated.append(q)
            rows = simulate(self.corpus, q)
        return [r for r in copy.deepcopy(rows)
                if r.get("info_hash") and r["info_hash"] != "0" * 40 and int(r.get("seeders") or 0) > 0]

    async def listing(self, apibay_id):
        return list(self.listings.get(str(apibay_id), []))

    @contextlib.contextmanager
    def patched(self):
        with patch.object(tool_module, "apibay_query", self.query), patch.object(scout, "listing", self.listing):
            yield self


def default_values() -> dict:
    """A household's default request settings (the effective contract a new
    account's request resolves to)."""
    from backend.agents.accounts import Preferences

    return Preferences().model_dump()


def _wanted(title):
    return sorted((int(s), int(e)) for s, episodes in (title.get("episodes") or {}).items() for e in episodes)


def _wanted_text(title):
    return ", ".join(f"S{s:02d}E{e:02d}" for s, e in _wanted(title)) or str(title.get("year") or "")


# ─── Offline replay ───────────────────────────────────────────────────────


async def replay_title(title, cassette, values=None) -> dict:
    """The scout's short list for one title, as find_releases builds it, scored
    against the copies a careful person would accept."""
    from backend.agents.models import Job

    values = values or default_values()
    job = Job(tmdb_id=title["tmdb_id"], media_type=title["media_type"], title=title["title"], year=title.get("year"),
              wanted_episodes={str(s): list(e) for s, e in (title.get("episodes") or {}).items()},
              preferred_quality=values["preferred_quality"], min_quality=values["min_quality"],
              audio_pref=values["audio_pref"], preferences={"values": values, "policy": {}},
              original_language=cassette.tmdb.get(f"/{title['media_type']}/{title['tmdb_id']}", {}).get("original_language", ""))
    targets = _wanted(title)[:12]
    season = targets[0][0] if targets else 1
    tb, searches, peeks = Replay([cassette]), [], []
    with tb.patched():
        names, exclude, namesakes = await scout.titles_for(tb, job, season, targets)
        rows, searched, dropped, failing = await scout.scout(tb, job, targets, names, (), exclude=exclude, log=searches,
                                                             peek_log=peeks)
    correct = set(cassette.correct)
    nothing = cassette.expect == "nothing"
    scored = bool(correct) or nothing
    rank = next((n for n, row in enumerate(rows, 1) if row["apibay_id"] in correct), None)
    right = (not rows) if nothing else rank == 1
    if failing:
        why = "the index failed"
    elif not rows:
        why = "nothing usable, as expected" if nothing else "nothing usable"
    elif nothing:
        why = "listed a copy where none is usable"
    elif rank == 1:
        why = "right copy first"
    elif rank:
        why = f"right copy at r{rank}"
    else:
        why = "no right copy listed" if scored else "unscored"
    return {
        "key": title["key"], "set": title.get("set", ""), "title": title["title"], "wanted": _wanted_text(title),
        "scored": scored, "right_first": right if scored else None, "rank": rank,
        "would_escalate": bool(failing or not rows or (scored and not nothing and rank is None)),
        "why": why, "searches": sum(1 for s in searches if not s.get("cached")),
        "cached": sum(1 for s in searches if s.get("cached")), "peeks": len(peeks), "kept": len(rows), "dropped": dropped,
        "queries": searched, "simulated_queries": len(tb.simulated), "search_titles": names, "excluded": exclude,
        "namesakes": namesakes,
        "short_list": [{"rid": r["rid"], "name": r["name"], "seeders": r["seeders"], "quality": r["quality"],
                        "coverage": r["coverage"], "right": r["apibay_id"] in correct} for r in rows],
    }


def _stats(results):
    scored = [r for r in results if r["scored"]]
    searches = sorted(r["searches"] for r in results)
    return {
        "titles": len(results), "scored": len(scored),
        "right_first": sum(1 for r in scored if r["right_first"]),
        "right_first_rate": round(sum(1 for r in scored if r["right_first"]) / len(scored), 3) if scored else None,
        "would_escalate": sum(1 for r in results if r["would_escalate"]),
        "escalation_rate": round(sum(1 for r in results if r["would_escalate"]) / len(results), 3) if results else None,
        "searches_median": statistics.median(searches) if searches else None,
        "searches_max": max(searches) if searches else None,
        "peeks": sum(r["peeks"] for r in results),
        "simulated_queries": sum(r["simulated_queries"] for r in results),
    }


def _digest(paths):
    hashed = hashlib.sha256()
    for path in paths:
        hashed.update(str(path).encode())
        hashed.update((ROOT / path).read_bytes() if (ROOT / path).exists() else b"")
    return hashed.hexdigest()


def _revision():
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        changed = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL))
    except (OSError, subprocess.CalledProcessError):
        return "", None
    return revision, changed


async def offline(titles, cassettes, directory=CASSETTES) -> dict:
    results, missing = [], []
    for title in titles:
        if title["key"] in cassettes:
            results.append(await replay_title(title, cassettes[title["key"]]))
        else:
            missing.append(title["key"])
    revision, changed = _revision()
    cassette_files = sorted(str(p.resolve().relative_to(ROOT)) if p.resolve().is_relative_to(ROOT) else str(p)
                            for p in Path(directory).rglob("*.json"))
    return {
        "version": VERSION, "mode": "offline", "revision": revision, "working_tree_changed": changed,
        "scout_sha256": _digest(SCOUT_SOURCES), "cassettes_sha256": _digest(cassette_files),
        "model_calls": 0, "cost_usd": 0,
        "sources": sorted({cassettes[r["key"]].source.split(":")[0] for r in results}),
        "summary": _stats(results),
        "by_set": {name: _stats([r for r in results if r["set"] == name]) for name in dict.fromkeys(r["set"] for r in results)},
        "missing_cassettes": missing, "results": results,
    }


def offline_report(result, before=None) -> str:
    s = result["summary"]
    lines = [
        f"**Offline replay: {s['titles']} titles, no model calls** · right copy first {s['right_first']}/{s['scored']} · "
        f"would escalate {s['would_escalate']} ({s['escalation_rate']:.0%}) · searches median {s['searches_median']}, "
        f"max {s['searches_max']} · peeks {s['peeks']} · searches answered from a corpus (not a recording) {s['simulated_queries']}",
        "",
        "| Set | Title | Wanted | Result | Escalate | Searches | Peeks | Short list |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in result["results"]:
        lines.append(f"| {r['set']} | {r['title']} | {r['wanted']} | {r['why']} | {'yes' if r['would_escalate'] else 'no'} | "
                     f"{r['searches']} | {r['peeks']} | {r['kept']} |")
    if result.get("missing_cassettes"):
        lines += ["", "No cassette: " + ", ".join(result["missing_cassettes"])]
    if before:
        b = before["summary"]
        lines += ["", f"**Before → after**: right first {b['right_first']}/{b['scored']} → {s['right_first']}/{s['scored']} · "
                      f"would escalate {b['would_escalate']} → {s['would_escalate']} · searches median "
                      f"{b['searches_median']} → {s['searches_median']}"]
        old = {r["key"]: r for r in before["results"]}
        for r in result["results"]:
            was = old.get(r["key"])
            if was and (was["why"], was["searches"]) != (r["why"], r["searches"]):
                lines.append(f"- {r['title']}: {was['why']} in {was['searches']} searches → {r['why']} in {r['searches']}")
    return "\n".join(lines)


# ─── Live, search only ────────────────────────────────────────────────────


def _response(name, args, tokens=1800):
    """A provider answer with one tool call, for dry runs of the live harness."""
    block = SimpleNamespace(type="tool_use", id="tu_" + hashlib.sha1(f"{name}{time.time_ns()}".encode()).hexdigest()[:16],
                            name=name, input=args)
    block.to_dict = lambda: {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    usage = SimpleNamespace(input_tokens=tokens, output_tokens=60, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(content=[block], usage=usage)


def scripted_model():
    """A stand-in Fetch model for dry runs: search, propose r1, then sleep."""

    async def call(session, system, tools):
        asked = [b.get("name") for m in session.messages if m.get("role") == "assistant" and isinstance(m.get("content"), list)
                 for b in m["content"] if b.get("type") == "tool_use"]
        results = " ".join(str(b.get("content")) for m in session.messages if isinstance(m.get("content"), list)
                           for b in m["content"] if b.get("type") == "tool_result")
        if "find_releases" not in asked:
            return _response("find_releases", {})
        if "propose_release" not in asked and "r1 ·" in results:
            return _response("propose_release", {"release": "r1", "reason": "the best seeded copy that fits"})
        return _response("wake_me", {"reason": "Waiting for the download."})

    return call


class LiveBench:
    """Real Fetch Agents on an isolated state directory, stopped once each
    request's download is chosen. Adding a transfer is intercepted at the
    storage node (no torrent is added); everything before it is real."""

    POLL = 2.0
    TIMER = 5.0
    LONG_WAIT = 20 * 60  # an agent asleep longer than this without a choice has decided to wait

    def __init__(self, state, titles, *, batch, concurrency=5, minutes=45, max_dollars=5.0, replay=None, model=None,
                 record=None):
        self.state, self.titles, self.batch = Path(state), titles, batch
        self.concurrency, self.minutes, self.max_dollars = concurrency, minutes, max_dollars
        self.replay, self.model, self.record = replay, model, Path(record) if record else None
        self.chosen, self.records, self.fired = {}, [], {}
        self.index_log, self.listing_log, self.tmdb_log = {}, {}, {}

    async def setup(self):
        from backend.agents.service import AgentService
        from backend.models import SparrowConfig
        from backend.storage import Storage

        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "staging").mkdir(exist_ok=True)
        self.storage = Storage(str(self.state))
        await self.storage.load_all()
        # Keys come from the environment (PROVIDER_API_KEY, ALTPROVIDER_API_KEY,
        # TMDB_API_KEY) and are never written into the run's state.
        await self.storage.save_config(SparrowConfig(
            staging_dir=str(self.state / "staging"), max_active_transfers=10_000, onboarding_complete=True,
        ))
        self.service = AgentService(self.storage, str(self.state), AsyncMock())
        self.store = self.service.store
        if self.service.accounts.has_users():
            raise SystemExit(f"{self.state} already holds a household. Use a new state directory for each run.")
        # The household's default request settings, as a new owner's requests resolve them.
        self.owner = self.service.accounts.create_user("benchmark", "benchmark-" + os.urandom(12).hex(), "Benchmark",
                                                       bootstrap=True)

    @contextlib.contextmanager
    def intercepted(self):
        """Transfers are chosen, never started: the storage node's download
        operations answer without a download app."""
        from backend.agents.nodes import Nodes

        real = Nodes.execute
        bench = self

        async def execute(nodes, node_id, kind, args=None, *, operation_id=None, job=None, timeout=60):
            if kind == "download_add":
                bench.chosen.setdefault(job.id if job else "", {**(args or {}), "ts": time.time()})
                return {"hash": (args or {}).get("info_hash", "")}
            if kind == "download_status":
                return {"status": "downloading", "progress": 0.0}
            if kind.startswith("download_") or kind == "discard_staging":
                return {"confirmed": True, "removed": True}
            return await real(nodes, node_id, kind, args, operation_id=operation_id, job=job, timeout=timeout)

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(Nodes, "execute", execute))
            stack.enter_context(patch.object(Toolbox, "select_soon", AsyncMock()))
            if self.replay is not None:
                stack.enter_context(self.replay.patched())
                stack.enter_context(patch.object(self.service.toolbox, "tmdb_get", self.replay.tmdb_get))
            if self.model is not None:
                from backend.agents import acquisition_review

                stack.enter_context(patch.object(self.service.runtime, "_call_api", self.model))
                verdict = ({"approve": True, "reason": "It fits the request.", "instead": "", "queries": []}, 0.002,
                           self.service.smart_model(), {"input_tokens": 600, "output_tokens": 40})
                stack.enter_context(patch.object(acquisition_review, "review", AsyncMock(return_value=verdict)))
            if self.record is not None:
                self._recorders(stack)
            yield

    def _recorders(self, stack):
        real_query, real_listing, real_tmdb = tool_module.apibay_query, scout.listing, self.service.toolbox.tmdb_get

        async def query(q, cat="0", *, strict=False):
            rows = await real_query(q, cat, strict=strict)
            self.index_log[_normal(q)] = rows
            return rows

        async def listing(apibay_id):
            entries = await real_listing(apibay_id)
            self.listing_log[str(apibay_id)] = entries
            return entries

        async def tmdb_get(path, **params):
            answer = await real_tmdb(path, **params)
            self.tmdb_log[tmdb_key(path, params)] = answer
            return answer

        stack.enter_context(patch.object(tool_module, "apibay_query", query))
        stack.enter_context(patch.object(scout, "listing", listing))
        stack.enter_context(patch.object(self.service.toolbox, "tmdb_get", tmdb_get))

    async def verify(self, title):
        """The title's TMDB id, checked by name and year; searched for when the
        set's id names something else."""
        kind = "tv" if title["media_type"] == "tv" else "movie"
        name, date = ("name", "first_air_date") if kind == "tv" else ("title", "release_date")

        def same(found):
            year = str(found.get(date) or "")[:4]
            names = {scout.compact(found.get(name) or ""), scout.compact(found.get("original_" + name) or "")}
            return scout.compact(title["title"]) in names and (not title.get("year") or not year.isdigit()
                                                                 or abs(int(year) - int(title["year"])) <= 1)

        try:
            if same(await self.service.toolbox.tmdb_get(f"/{kind}/{title['tmdb_id']}")):
                return title["tmdb_id"]
        except ToolError:
            pass
        year = {"first_air_date_year" if kind == "tv" else "year": title.get("year")} if title.get("year") else {}
        found = await self.service.toolbox.tmdb_get(f"/search/{kind}", query=title["title"], **year)
        match = next((r for r in found.get("results") or [] if same(r)), None)
        return match["id"] if match else None

    async def start(self, title):
        record = {"key": title["key"], "set": title.get("set", ""), "title": title["title"], "wanted": _wanted_text(title),
                  "started": time.time(), "job_id": "", "outcome": "searching"}
        self.records.append(record)
        try:
            ident = await self.verify(title)
            if not ident:
                record.update(outcome="skipped", detail="TMDB has no title by this name and year")
                return record
            job = await self.service.create_job(ident, title.get("episodes"), title["media_type"], user_id=self.owner["id"],
                                                library_id="local")
        except (ToolError, ValueError) as exc:
            record.update(outcome="skipped", detail=str(exc)[:200])
            return record
        record.update(job_id=job.id, tmdb_id=ident)
        ledger.tag_batch(self.store, self.batch, [job.id])
        return record

    def spent(self) -> float:
        jobs = [r["job_id"] for r in self.records if r["job_id"]]
        if not jobs:
            return 0.0
        with ledger._db(self.store) as db:
            return float(db.execute(f"SELECT COALESCE(SUM(cost),0) FROM ledger_calls WHERE job_id IN ({','.join('?' * len(jobs))})",
                                    jobs).fetchone()[0])

    def _stop(self, record, outcome, detail=""):
        """Close the request's agent and pause the request (a new revision, so
        no tool already running can act for it), keeping every ledger row."""
        from backend.agents.models import JobStatus, SessionStatus

        job = self.store.get_job(record["job_id"])
        session = self.store.get_session(job.session_id) if job else None
        if session and session.status != SessionStatus.CLOSED:
            session.status, session.closed_at = SessionStatus.CLOSED, time.time()
            session.wake_at, session.close_reason = 0.0, f"Search benchmark stopped it: {outcome}."
            self.store.save_session(session)
        if job:
            job.status, job.revision, job.state_line = JobStatus.PAUSED, job.revision + 1, f"Benchmark stopped: {outcome}."
            self.store.save_job(job)
        record.update(outcome=outcome, detail=detail, stopped=time.time())

    def settle(self, record) -> bool:
        """Whether a request is finished for the benchmark (and stop it if so):
        its download was chosen, its agent closed it, it ran out of time, or
        its agent went to sleep for long without choosing (it decided to wait)."""
        from backend.agents.models import SessionStatus

        job = self.store.get_job(record["job_id"])
        session = self.store.get_session(job.session_id) if job else None
        if not job or not session:
            record["outcome"] = "lost"
            return True
        running = session.status == SessionStatus.RUNNING or self.service.runtime._lock(session.id).locked() \
            or bool(self.store.pending_events(session.id))
        if running:
            return False
        if record["job_id"] in self.chosen:
            self._stop(record, "chosen")
        elif session.status == SessionStatus.CLOSED:
            record.update(outcome="closed", detail=(session.close_reason or job.status.value)[:200], stopped=time.time())
        elif time.time() - record["started"] > self.minutes * 60:
            self._stop(record, "timed out")
        elif not session.wake_at or session.wake_at - time.time() > self.LONG_WAIT:
            self._stop(record, "waiting", (session.wake_reason or "no copy chosen")[:200])
        else:
            return False
        return True

    async def _timers(self, active):
        from backend.agents.models import AgentKind, Event

        while True:
            await asyncio.sleep(self.TIMER)
            for session in self.store.due_sessions():
                if session.agent != AgentKind.FETCH or session.job_id not in active:
                    continue
                if self.fired.get(session.id) == session.wake_at:
                    continue
                self.fired[session.id] = session.wake_at
                await self.service.emit(Event(kind="timer", session_id=session.id, payload={
                    "description": f"Your wake timer fired. You were waiting on: {session.wake_reason or '(no reason recorded)'}"}))

    async def run(self) -> dict:
        await self.setup()
        pending, active = list(self.titles), {}
        began = time.time()
        with self.intercepted():
            timers = asyncio.create_task(self._timers(active))
            try:
                while pending or active:
                    over = self.spent() >= self.max_dollars
                    while pending and len(active) < self.concurrency and not over:
                        record = await self.start(pending.pop(0))
                        if record["job_id"]:
                            active[record["job_id"]] = record
                    await asyncio.sleep(self.POLL)
                    for job_id, record in list(active.items()):
                        if self.settle(record):
                            del active[job_id]
                    if self.spent() >= self.max_dollars:
                        for job_id, record in list(active.items()):
                            if not self.settle(record):
                                self._stop(record, "spending limit", f"the run reached ${self.max_dollars:.2f}")
                            del active[job_id]
                        for title in pending:
                            self.records.append({"key": title["key"], "set": title.get("set", ""), "title": title["title"],
                                                 "wanted": _wanted_text(title), "job_id": "", "outcome": "not started",
                                                 "detail": f"the run reached ${self.max_dollars:.2f}"})
                        pending = []
            finally:
                timers.cancel()
                for record in active.values():
                    self._stop(record, "interrupted")
                for task in list(self.service._wake_tasks):
                    task.cancel()
                await asyncio.gather(timers, *self.service._wake_tasks, return_exceptions=True)
        if self.record is not None:
            self.write_cassettes()
        return self.result(time.time() - began)

    def result(self, seconds) -> dict:
        summary = ledger.summarize(self.store, batch=self.batch)
        by_job = {t["job_id"]: t for t in summary["titles"]}
        titles = []
        for record in self.records:
            t = by_job.get(record["job_id"]) or {}
            chosen = self.chosen.get(record["job_id"]) or {}
            titles.append({
                **{k: record.get(k) for k in ("key", "set", "title", "wanted", "outcome")},
                "search_cost": t.get("search_cost", 0.0),
                "phases": {p: round(d, 5) for p, d in (t.get("phases") or {}).items() if p in ledger.SEARCH_PHASES and d},
                "model_calls": (t.get("calls") or {}).get("search", 0), "escalated": bool(t.get("escalations")),
                "searches": (t.get("searches") or {}).get("live", 0), "cached_searches": (t.get("searches") or {}).get("cached", 0),
                "rate_limited": (t.get("searches") or {}).get("rate_limited", 0),
                "picks": t.get("picks"), "seconds_to_choice": round(chosen["ts"] - record["started"]) if chosen else None,
                # Audit-only detail: release names and the agent's own words stay out of the public summary.
                "private": {"chosen": chosen.get("name", ""), "detail": record.get("detail", ""),
                            "escalations": t.get("escalations", []), "flags": t.get("flags", [])},
            })
        stats = summary["stats"]
        return {
            "version": VERSION, "mode": "live" if self.model is None else "dry", "batch": self.batch,
            "revision": _revision()[0], "state": str(self.state), "seconds": round(seconds),
            "models": {"fetch": self.service.fetch_model(), "smart": self.service.smart_model()},
            "targets": ledger.TARGETS,
            "stats": {k: stats.get(k) for k in ("titles", "search_total", "search_median", "search_p90", "search_mean",
                                                 "escalated", "escalation_rate", "search_by_phase", "live_searches",
                                                 "cached_searches", "rate_limited", "model_errors", "reviews")},
            "met": {k: stats[k] <= ledger.TARGETS[k] for k in ("search_median", "search_p90", "escalation_rate")} if stats else {},
            "titles": titles, "report": ledger.report(summary),
        }

    def write_cassettes(self):
        """What the indexer, pack listings and TMDB answered for each title, as
        cassettes the offline replay can read. Right copies are left to a person."""
        self.record.mkdir(parents=True, exist_ok=True)
        _, tools, _, _ = ledger.load(self.store, batch=self.batch)
        stamp = time.strftime("%Y-%m-%d")
        by_key = {t["key"]: t for t in self.titles}
        for record in self.records:
            if not record["job_id"]:
                continue
            mine = [t for t in tools if t["job_id"] == record["job_id"]]
            queries = {_normal(s["query"]) for t in mine for s in t["facts"].get("searches") or [] if s.get("query")}
            peeked = {str(p.get("id")) for t in mine for p in t["facts"].get("peeks") or []}
            ident = str(record.get("tmdb_id"))
            tmdb = {k: v for k, v in self.tmdb_log.items()
                    if f"/{ident}" in k or (k.startswith("/search/") and _normal(record["title"]) in _normal(urllib.parse.unquote_plus(k)))}
            chosen = (self.chosen.get(record["job_id"]) or {}).get("info_hash", "")
            index = {q: self.index_log[q] for q in sorted(queries) if q in self.index_log}
            ids = [r.get("id") for rows in index.values() for r in rows if str(r.get("info_hash", "")).lower() == chosen.lower()]
            title = {**by_key[record["key"]], "tmdb_id": record.get("tmdb_id")}
            (self.record / f"{record['key']}.json").write_text(json.dumps({
                "version": VERSION, "key": record["key"], "source": f"live {stamp}", "job": title, "index": index,
                "listings": {i: [[n, round(s / 1e6, 1)] for n, s in self.listing_log[i]] for i in sorted(peeked) if i in self.listing_log},
                "tmdb": tmdb, "correct": [], "chosen": ids[:1],
                "note": "Recorded live. 'correct' is empty: confirm the right copies by hand before scoring with this cassette.",
            }, indent=1, ensure_ascii=False) + "\n")


def public(result) -> dict:
    """A live result without release names or the agents' own words (the
    ledger report's flags quote them): safe to commit."""
    return {**{k: v for k, v in result.items() if k != "report"}, "state": "",
            "titles": [{k: v for k, v in t.items() if k != "private"} for t in result["titles"]]}


def live_report(result) -> str:
    stats, lines = result["stats"], []
    if stats.get("titles"):
        lines.append(f"**Live search-only run {result['batch']}: {stats['titles']} titles, ${stats['search_total']:.3f}** · "
                     f"median {ledger.money(stats['search_median'])} · p90 {ledger.money(stats['search_p90'])} · "
                     f"escalation {stats['escalation_rate']:.0%} · models {result['models']['fetch']} / {result['models']['smart']}")
        lines.append(ledger.targets_line(stats))
    lines += ["", "| Title | Wanted | Outcome | Search $ | Calls | Searches | Escalated | To choice |", "|---|---|---|---|---|---|---|---|"]
    for t in result["titles"]:
        waited = "" if t["seconds_to_choice"] is None else f"{t['seconds_to_choice'] / 60:.0f}m"
        lines.append(f"| {t['title']} | {t['wanted']} | {t['outcome']} | {t['search_cost']:.4f} | {t['model_calls']} | "
                     f"{t['searches']} | {'yes' if t['escalated'] else 'no'} | {waited} |")
    return "\n".join(lines)


async def live(args, titles, replay=None, model=None) -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    batch = args.batch or f"search-{stamp}"
    state = Path(args.state) if args.state else ARTIFACTS / f"search-live-{stamp}"
    if (state / "sparrow.db").exists():
        raise SystemExit(f"{state} already holds a run. Use a new --state directory.")
    bench = LiveBench(state, titles, batch=batch, concurrency=args.concurrency, minutes=args.minutes,
                      max_dollars=args.max_dollars, replay=replay, model=model, record=args.record)
    return await bench.run()


# ─── Pinning title sets ───────────────────────────────────────────────────


def titles_from_state(state, since=0.0) -> list:
    """The requests in an earlier run's state directory, as a title set (read only)."""
    path = Path(state) / "sparrow.db"
    if not path.exists():
        raise SystemExit(f"No sparrow.db in {state}.")
    with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        jobs = [json.loads(row[0]) for row in db.execute("SELECT data FROM jobs")]
    out, seen = [], set()
    for job in sorted(jobs, key=lambda j: j.get("created_at", 0)):
        if job.get("created_at", 0) < since:
            continue
        key = re.sub(r"[^a-z0-9]+", "-", scout._fold(job.get("title", "")).lower()).strip("-") or str(job.get("tmdb_id"))
        if key in seen:
            continue
        seen.add(key)
        entry = {"key": key, "title": job.get("title", ""), "media_type": job.get("media_type", "tv"),
                 "tmdb_id": job.get("tmdb_id"), "year": job.get("year")}
        if job.get("media_type", "tv") == "tv":
            entry["episodes"] = {str(s): sorted(int(e) for e in eps) for s, eps in (job.get("wanted_episodes") or {}).items()}
        out.append(entry)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="search_bench.py", description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    parser.add_argument("mode", choices=["offline", "live", "titles"])
    parser.add_argument("--set", action="append", dest="sets", help="a title set (repeatable; default: every set)")
    parser.add_argument("--titles", default=str(TITLES), help="the title sets file")
    parser.add_argument("--cassettes", default=str(CASSETTES), help="offline: the cassette directory")
    parser.add_argument("--output", help="write the result as JSON here")
    parser.add_argument("--compare", help="offline: an earlier result to compare with")
    parser.add_argument("--state", help="live: a new state directory (default under tests/evals/artifacts)")
    parser.add_argument("--batch", help="live: the ledger batch name (default search-<time>)")
    parser.add_argument("--concurrency", type=int, default=5, help="live: requests searching at once")
    parser.add_argument("--minutes", type=float, default=45, help="live: stop a request not chosen after this long")
    parser.add_argument("--max-dollars", type=float, default=5.0, help="live: stop the run once it has spent this much")
    parser.add_argument("--record", help="live: write cassettes of what the indexer and TMDB answered here")
    parser.add_argument("--from-state", help="titles: an earlier run's state directory")
    parser.add_argument("--since", type=float, default=0.0, help="titles: requests created from this unix time")
    parser.add_argument("--name", help="titles: the set name")
    parser.add_argument("--write", action="store_true", help="titles: save the set into the title sets file")
    args = parser.parse_args(argv)
    sets = title_sets(args.titles)
    if args.mode == "titles":
        if not args.from_state or not args.name:
            parser.error("titles needs --from-state DIR and --name NAME")
        found = titles_from_state(args.from_state, args.since)
        if args.write:
            data = json.loads(Path(args.titles).read_text())
            data["sets"][args.name] = found
            Path(args.titles).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            print(f"{len(found)} titles saved as set {args.name}.")
        else:
            print(json.dumps(found, indent=1, ensure_ascii=False))
        return None
    titles = chosen_titles(sets, args.sets)
    if args.mode == "offline":
        result = asyncio.run(offline(titles, load_cassettes(args.cassettes, sets), args.cassettes))
        before = json.loads(Path(args.compare).read_text()) if args.compare else None
        print(offline_report(result, before))
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
        return result
    if os.getenv("SPARROW_RUN_LIVE_BENCH") != "1":
        raise SystemExit("The live benchmark makes paid model calls and real indexer and TMDB searches (no downloads). "
                         "Set SPARROW_RUN_LIVE_BENCH=1 to run it.")
    result = asyncio.run(live(args, titles))
    print(live_report(result) + "\n\n" + result["report"])
    print(f"\nAudit it: .venv/bin/python -m backend.agents.ledger audit --data {result['state']} --batch {result['batch']}")
    (Path(result["state"]) / "search-live-result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    if args.output:
        Path(args.output).write_text(json.dumps(public(result), indent=1, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    main()
