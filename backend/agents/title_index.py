"""Instant title search over the catalogue's well-known films and series.

Find searches a copy of the catalogue held in memory, not the catalogue service,
so results follow every keystroke and survive typos. The copy
(``titles.sqlite3`` in the data folder) holds films with 10+ votes and series
with 5+, plus the year's new, upcoming and trending titles. It builds itself as
soon as a TMDB key is present, refreshes new and upcoming titles daily and
everything weekly, and remembers titles the live catalogue search turns up.

Matching: every query word must match a word in the title, exactly, as the
start of the word still being typed, or within a small typo allowance (none
under four letters, one up to seven, two from eight; swapped neighbours count
once). Ranking: how well known the title is, less a step per typo, plus a lift
for the exact title or a title that starts with the query. No model call.
"""

from __future__ import annotations

import asyncio
import bisect
import heapq
import logging
import math
import re
import sqlite3
import time
import unicodedata
from array import array
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

import httpx
from rapidfuzz import process
from rapidfuzz.distance import OSA

log = logging.getLogger(__name__)

TMDB = "https://api.themoviedb.org/3"
POSTER = "https://image.tmdb.org/t/p/w185"
MIN_VOTES = {"movie": 10, "tv": 5}
FIRST_YEAR = 1874
FRESH_PAGES = 50  # new and upcoming titles, by popularity, per kind
TRENDING_PAGES = 10
DAY, WEEK = 86400, 7 * 86400
CONCURRENCY = 6  # well inside TMDB's request allowance
MAX_PAGES = 500  # the most pages the catalogue lists for one query
RETRY_DELAY = 1.0
RESULTS = 20
SHORT_PREFIX = 400  # a word of one to three letters: this many best-known titles
CANDIDATES = 3000  # most a ranking looks at

# One row: (media_type, tmdb_id, title, original_title, year, popularity, votes, rating)
Row = tuple

_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "ii": "2", "iii": "3",
    "iv": "4", "vi": "6", "vii": "7", "viii": "8", "ix": "9",
}


def plain(text: str) -> str:
    """Lowercase letters and digits: accents, punctuation and "the &" folded."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace("&", " and ").replace("'", "").replace("’", "")
    return re.sub(r"[\W_]+", " ", text).strip()


def words(text: str) -> list[str]:
    return plain(text).split()


def _year(token: str) -> bool:
    return len(token) == 4 and token.isdigit() and 1870 <= int(token) <= 2100


_EPISODE = re.compile(r"s\d{1,2}(?:e\d{1,3})?|e\d{1,3}")


def show_words(tokens: list[str]) -> list[str]:
    """Leave out a trailing season or episode: "breaking bad season 2" and
    "the office s03e04" search for the show."""
    tokens = list(tokens)
    while len(tokens) > 1:
        last = tokens[-1]
        if _EPISODE.fullmatch(last) or last in ("season", "episode", "complete"):
            tokens.pop()
        elif len(tokens) > 2 and tokens[-2] in ("season", "series", "episode") and (last.isdigit() or last in _NUMBERS):
            del tokens[-2:]
        else:
            break
    return tokens


def allowance(token: str) -> int:
    """Typos a word may carry: none under four letters, one to seven, two after."""
    if token.isdigit():
        return 0
    return 0 if len(token) < 4 else 1 if len(token) < 8 else 2


def fame(votes: int, rating: float, popularity: float) -> float:
    """How well known a title is. A rating counts in proportion to its votes, so
    a 9.8 from twelve people never outranks a film everyone has seen."""
    votes, rating, popularity = votes or 0, rating or 0.0, popularity or 0.0
    weighted = (votes * rating + 300 * 6.4) / (votes + 300)
    return math.log10(votes + 1) + 0.35 * math.log10(popularity + 1) + 0.6 * (weighted - 6.4)


class TitleIndex:
    """An immutable, searchable snapshot of the titles. Built in about two
    seconds from 140,000 rows; searched in milliseconds."""

    def __init__(self, rows: Iterable[Row]):
        ranked = sorted(rows, key=lambda r: fame(r[6], r[7], r[5]), reverse=True)
        # A title's number is its rank, so every word's title list is already
        # in order of fame and the best matches come off the front.
        self.size = len(ranked)
        self.kinds = bytes(1 if r[0] == "tv" else 0 for r in ranked)
        self.ids = array("I", (int(r[1]) for r in ranked))
        self.fame = array("d", (fame(r[6], r[7], r[5]) for r in ranked))
        self.names = [" ".join(words(r[2])) for r in ranked]
        title_words: list[list[str]] = []
        postings: dict[str, list[int]] = defaultdict(list)
        for i, row in enumerate(ranked):
            found = set(self.names[i].split())
            original = plain(row[3])
            if original and original.isascii():
                found.update(original.split())
            found.update(_NUMBERS[w] for w in list(found) if w in _NUMBERS)
            if row[4]:
                found.add("#" + str(row[4]))  # the year; "#" never comes from typing
            title_words.append(list(found))
            for w in found:
                postings[w].append(i)
        self.vocab = sorted(postings)
        self.word_id = {w: n for n, w in enumerate(self.vocab)}
        self.postings = [array("I", postings[w]) for w in self.vocab]
        del postings
        self.flat = array("I")
        self.offsets = array("I", [0])
        for found in title_words:
            self.flat.extend(self.word_id[w] for w in found)
            self.offsets.append(len(self.flat))
        del title_words
        # Typo matching compares only against words of about the same length,
        # and the word being typed against word beginnings of that length.
        by_length: dict[int, list[str]] = defaultdict(list)
        beginnings: dict[int, set[str]] = defaultdict(set)
        for w in self.vocab:
            if w[0] == "#" or w.isdigit():
                continue
            by_length[len(w)].append(w)
            for n in range(4, min(len(w), 14) + 1):
                beginnings[n].add(w[:n])
        self.by_length = dict(by_length)
        self.beginnings = {n: sorted(p) for n, p in beginnings.items()}
        # "spiderman", "starwars": titles with their spaces taken out.
        squashed = sorted((n.replace(" ", ""), i) for i, n in enumerate(self.names) if " " in n)
        self.squashed = [s for s, _ in squashed]
        self.squashed_ids = array("I", (i for _, i in squashed))
        self._expansions: dict[tuple[str, bool], dict[int, int]] = {}
        self._shorts: dict[str, dict[int, int]] = {}

    def key(self, i: int) -> tuple[str, int]:
        return ("tv" if self.kinds[i] else "movie", self.ids[i])

    def _span(self, start: str) -> range:
        """Word numbers of every word beginning with ``start``."""
        lo = bisect.bisect_left(self.vocab, start)
        return range(lo, bisect.bisect_left(self.vocab, start + "￿", lo))

    def _expand(self, token: str, typing: bool) -> dict[int, int]:
        """Title words one query word can stand for, with the typos each costs."""
        cached = self._expansions.get((token, typing))
        if cached is not None:
            return cached
        out: dict[int, int] = {}
        if typing:
            out.update(dict.fromkeys(self._span(token), 0))
        elif token in self.word_id:
            out[self.word_id[token]] = 0
        for alias in (_NUMBERS.get(token), "#" + token if _year(token) else None):
            if alias in self.word_id:
                out.setdefault(self.word_id[alias], 0)
        k = allowance(token)
        if k:
            size = len(token)
            for n in range(size - k, size + k + 1):
                if typing and n >= 4:
                    for start, d, _ in process.extract(
                        token, self.beginnings.get(n, ()), scorer=OSA.distance, score_cutoff=k, limit=None
                    ):
                        for w in self._span(start):
                            if d < out.get(w, 99):
                                out[w] = int(d)
                for w, d, _ in process.extract(
                    token, self.by_length.get(n, ()), scorer=OSA.distance, score_cutoff=k, limit=None
                ):
                    number = self.word_id[w]
                    if d < out.get(number, 99):
                        out[number] = int(d)
        if len(self._expansions) > 4096:
            self._expansions.clear()
        self._expansions[(token, typing)] = out
        return out

    def search(self, query: str, limit: int = RESULTS) -> tuple[list[tuple[int, int]], int]:
        """Best matches as (title number, typos), and how many trailing words had
        to be left out to find any ("breaking bad season 2" → "breaking bad")."""
        typing = not query[-1:].isspace()  # a trailing space finishes the word
        tokens = words(query)
        if not tokens:
            return [], 0
        shown = show_words(tokens)
        if len(shown) < len(tokens):
            tokens, typing = shown, False
        if len(tokens) == 1 and typing and allowance(tokens[0]) == 0 and not tokens[0].isdigit():
            return self._rank(self._short(tokens[0]), tokens, limit), 0
        found: dict[int, int] = {}
        dropped = 0
        # Up to half the words may go, from the end, keeping at least two.
        for dropped in range(max(0, min(len(tokens) // 2, len(tokens) - 2)) + 1):
            found = self._match(tokens[: len(tokens) - dropped], typing and not dropped)
            if found:
                break
        squashed = "".join(tokens)
        if len(squashed) >= 4 and len(tokens) <= 3:
            lo = bisect.bisect_left(self.squashed, squashed)
            hi = bisect.bisect_left(self.squashed, squashed + "￿", lo)
            for i in heapq.nsmallest(SHORT_PREFIX, self.squashed_ids[lo:hi]):
                found[i] = 0
        return self._rank(found, tokens, limit), dropped if found else 0

    def _short(self, token: str) -> dict[int, int]:
        """A word of one to three letters being typed: the best-known titles
        holding a word that starts with it."""
        cached = self._shorts.get(token)
        if cached is None:
            cached = dict.fromkeys(_smallest((self.postings[w] for w in self._span(token)), SHORT_PREFIX), 0)
            self._shorts[token] = cached
        return cached

    def _match(self, tokens: list[str], typing: bool) -> dict[int, int]:
        expansions = [self._expand(t, typing and n == len(tokens) - 1) for n, t in enumerate(tokens)]
        if not all(expansions):
            return {}
        sizes = [sum(len(self.postings[w]) for w in e) for e in expansions]
        order = sorted(range(len(tokens)), key=sizes.__getitem__)
        found: dict[int, int] = {}
        for w, d in expansions[order[0]].items():
            for i in self.postings[w]:
                if d < found.get(i, 99):
                    found[i] = d
        rest = [expansions[n] for n in order[1:]]
        if not rest:
            return found
        out: dict[int, int] = {}
        flat, offsets = self.flat, self.offsets
        for i in sorted(found):  # best known first, so a long list can stop early
            total = found[i]
            held = flat[offsets[i] : offsets[i + 1]]
            for e in rest:
                best = min((e[w] for w in held if w in e), default=None)
                if best is None:
                    break
                total += best
            else:
                out[i] = total
                if len(out) >= CANDIDATES:
                    break
        return out

    def _rank(self, found: dict[int, int], tokens: list[str], limit: int) -> list[tuple[int, int]]:
        if len(found) > CANDIDATES:
            found = {i: found[i] for i in heapq.nsmallest(CANDIDATES, found)}
        query = " ".join(tokens)
        squashed = query.replace(" ", "")
        scored = []
        for i, typos in found.items():
            name = self.names[i]
            score = self.fame[i] - 1.5 * typos
            if name == query or name.replace(" ", "") == squashed:
                score += 1.5
            elif name.startswith(query) or name.replace(" ", "").startswith(squashed):
                score += 0.6
            scored.append((-score, i, typos))
        return [(i, typos) for _, i, typos in heapq.nsmallest(limit, scored)]


def _smallest(lists: Iterable[array], n: int) -> list[int]:
    """The n smallest distinct numbers across sorted lists."""
    out: list[int] = []
    for number in heapq.merge(*lists):
        if not out or number != out[-1]:
            out.append(number)
            if len(out) >= n:
                break
    return out


def card(row: sqlite3.Row | dict) -> dict:
    return {
        "tmdb_id": row["tmdb_id"],
        "media_type": row["media_type"],
        "title": row["title"],
        "year": row["year"] or "",
        "poster_url": f"{POSTER}{row['poster_path']}" if row["poster_path"] else None,
        "rating": round(float(row["rating"] or 0), 1),
    }


def _row(kind: str, result: dict) -> Optional[tuple]:
    """A catalogue result as a stored row, or None for people and adult titles."""
    title = result.get("title") or result.get("name")
    if not title or result.get("adult") or not result.get("id"):
        return None
    return (
        kind,
        int(result["id"]),
        title,
        result.get("original_title") or result.get("original_name") or "",
        (result.get("release_date") or result.get("first_air_date") or "")[:4],
        float(result.get("popularity") or 0),
        int(result.get("vote_count") or 0),
        float(result.get("vote_average") or 0),
        result.get("poster_path") or "",
    )


class TitleSearch:
    """The household's title search: the stored copy, its refreshes and the
    in-memory index, with the live catalogue search as a fallback."""

    def __init__(self, data_dir: str | Path, tmdb_key: Callable[[], str], transport=None):
        self.path = Path(data_dir) / "titles.sqlite3"
        self.tmdb_key = tmdb_key
        self.transport = transport
        self.index: Optional[TitleIndex] = None
        self.built_at = 0.0
        self.changed = False  # remembered titles not yet in the index
        self._client: Optional[httpx.AsyncClient] = None
        self._limit = asyncio.Semaphore(CONCURRENCY)
        self._task: Optional[asyncio.Task] = None
        self._live_cache: dict[str, tuple[float, list[dict]]] = {}
        self._schema()

    # Stored copy -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _schema(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS titles (
                    media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL, title TEXT NOT NULL,
                    original_title TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',
                    popularity REAL NOT NULL DEFAULT 0, votes INTEGER NOT NULL DEFAULT 0,
                    rating REAL NOT NULL DEFAULT 0, poster_path TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL, seen REAL NOT NULL,
                    PRIMARY KEY (media_type, tmdb_id))"""
            )
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _meta(self, key: str) -> float:
        with self._connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return float(row["value"]) if row else 0.0

    def _set_meta(self, key: str, value: float):
        with self._connect() as db:
            db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def store(self, rows: Iterable[tuple], source: str = "catalogue", seen: Optional[float] = None):
        """Add or update rows. A remembered search result never demotes a
        catalogue title, and a catalogue refresh adopts a remembered one."""
        seen = time.time() if seen is None else seen
        with self._connect() as db:
            db.executemany(
                """INSERT INTO titles VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(media_type, tmdb_id) DO UPDATE SET title=excluded.title,
                original_title=excluded.original_title, year=excluded.year,
                popularity=excluded.popularity, votes=excluded.votes, rating=excluded.rating,
                poster_path=excluded.poster_path, seen=excluded.seen,
                source=CASE WHEN titles.source='catalogue' THEN 'catalogue' ELSE excluded.source END""",
                [(*row, source, seen) for row in rows],
            )

    def _rows(self) -> list[Row]:
        with self._connect() as db:
            return [tuple(r) for r in db.execute(
                "SELECT media_type, tmdb_id, title, original_title, year, popularity, votes, rating FROM titles"
            )]

    def count(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) FROM titles").fetchone()[0]

    def rebuild(self):
        """Load the stored copy into a fresh index and swap it in."""
        rows = self._rows()
        self.changed = False
        self.index = TitleIndex(rows) if rows else None
        self.built_at = time.time()

    def _cards(self, keys: list[tuple[str, int]]) -> list[dict]:
        if not keys:
            return []
        with self._connect() as db:
            rows = {
                (r["media_type"], r["tmdb_id"]): r
                for r in db.execute(
                    "SELECT * FROM titles WHERE " + " OR ".join(["(media_type=? AND tmdb_id=?)"] * len(keys)),
                    [part for key in keys for part in key],
                )
            }
        return [card(rows[k]) for k in keys if k in rows]

    # Searching -------------------------------------------------------------

    def search(self, query: str) -> tuple[list[dict], bool]:
        """Index matches, and whether the live catalogue might know better
        (the index isn't built, or nothing matched cleanly)."""
        index = self.index
        if index is None:
            return [], True
        found, dropped = index.search(query)
        cards = self._cards([index.key(i) for i, _ in found])
        clean = 0 if dropped else sum(1 for _, typos in found if typos == 0)
        return cards, clean == 0 or len(found) < 3

    async def suggest(self, query: str, wide: bool = False) -> dict:
        """What Find shows. ``more`` asks the page to follow up with ``wide``,
        which adds the live catalogue's matches below the index's."""
        cards, more = await asyncio.to_thread(self.search, query)
        if wide and more:
            have = {(c["media_type"], c["tmdb_id"]) for c in cards}
            cards += [c for c in await self.live(query) if (c["media_type"], c["tmdb_id"]) not in have]
        return {"titles": cards[:RESULTS], "more": more and not wide}

    async def live(self, query: str) -> list[dict]:
        """The catalogue's own search, for titles the index doesn't hold yet.
        Results are kept for a day and remembered in the index."""
        key = plain(query)
        hit = self._live_cache.get(key)
        if hit and time.time() - hit[0] < DAY:
            return hit[1]
        try:
            data = await self._get("/search/multi", {"query": query, "include_adult": "false", "page": "1"}, limit=False)
        except Exception as exc:
            log.info("Live title search failed: %s", exc)
            return []
        rows = [row for r in data.get("results", []) if r.get("media_type") in ("movie", "tv")
                for row in [_row(r["media_type"], r)] if row]
        cards = [card(dict(zip(("media_type", "tmdb_id", "title", "original_title", "year", "popularity",
                                "votes", "rating", "poster_path"), row))) for row in rows]
        if rows:
            await asyncio.to_thread(self.store, rows, "search")
            self.changed = True
        if len(self._live_cache) > 512:
            self._live_cache.clear()
        self._live_cache[key] = (time.time(), cards)
        return cards

    # Refreshing ------------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=20, transport=self.transport,
                limits=httpx.Limits(max_connections=CONCURRENCY + 4, max_keepalive_connections=CONCURRENCY + 4),
            )
        return self._client

    async def _get(self, path: str, params: dict, limit: bool = True) -> dict:
        key = self.tmdb_key()
        if not key:
            raise RuntimeError("No TMDB key")
        response = None
        for attempt in range(5):
            if limit:
                async with self._limit:
                    response = await self._http().get(TMDB + path, params={"api_key": key, **params})
            else:
                response = await self._http().get(TMDB + path, params={"api_key": key, **params})
            if response.status_code == 429 or response.status_code >= 500:
                await asyncio.sleep(float(response.headers.get("retry-after") or RETRY_DELAY) + attempt * RETRY_DELAY)
                continue
            break
        assert response is not None
        response.raise_for_status()
        return response.json()

    async def _pages(self, path: str, params: dict, kind: str, most: int = 0, first: Optional[dict] = None) -> list[tuple]:
        first = first or await self._get(path, {**params, "page": 1})
        pages = min(int(first.get("total_pages") or 0), most or MAX_PAGES)
        rest = await asyncio.gather(*[self._get(path, {**params, "page": p}) for p in range(2, pages + 1)])
        rows = []
        for data in (first, *rest):
            for result in data.get("results", []):
                row = _row(result.get("media_type") or kind, result)
                if row and row[0] in ("movie", "tv"):
                    rows.append(row)
        return rows

    async def _span(self, kind: str, start: date, end: date) -> list[tuple]:
        """Every well-known title first shown between two dates. The catalogue
        lists at most MAX_PAGES pages per query, so a fuller span is halved."""
        field = "primary_release_date" if kind == "movie" else "first_air_date"
        params = {
            f"{field}.gte": start.isoformat(), f"{field}.lte": end.isoformat(),
            "vote_count.gte": MIN_VOTES[kind], "sort_by": "vote_count.desc",
            "include_adult": "false", "language": "en-US",
        }
        first = await self._get(f"/discover/{kind}", {**params, "page": 1})
        if int(first.get("total_pages") or 0) > MAX_PAGES and start < end:
            middle = start + (end - start) // 2
            halves = await asyncio.gather(self._span(kind, start, middle), self._span(kind, middle + timedelta(days=1), end))
            return halves[0] + halves[1]
        return await self._pages(f"/discover/{kind}", params, kind, first=first)

    async def _fresh(self) -> list[tuple]:
        """The year's new and upcoming titles, and what's trending, whatever
        their votes: a film people are waiting for has none yet."""
        today = date.today()
        jobs = [
            self._pages("/discover/movie", {
                "primary_release_date.gte": (today - timedelta(days=365)).isoformat(),
                "primary_release_date.lte": (today + timedelta(days=730)).isoformat(),
                "sort_by": "popularity.desc", "include_adult": "false", "language": "en-US"}, "movie", FRESH_PAGES),
            self._pages("/discover/tv", {
                "first_air_date.gte": (today - timedelta(days=365)).isoformat(),
                "first_air_date.lte": (today + timedelta(days=365)).isoformat(),
                "sort_by": "popularity.desc", "include_adult": "false", "language": "en-US"}, "tv", FRESH_PAGES),
            self._pages("/trending/movie/week", {"language": "en-US"}, "movie", TRENDING_PAGES),
            self._pages("/trending/tv/week", {"language": "en-US"}, "tv", TRENDING_PAGES),
        ]
        return [row for rows in await asyncio.gather(*jobs) for row in rows]

    async def refresh(self, full: bool):
        """Fetch the catalogue (everything, or just new and trending titles),
        store it and swap in a new index."""
        began = time.time()
        rows = await self._fresh()
        failed = 0
        if full:
            last = date.today().year + 3
            spans = await asyncio.gather(*[
                self._span(kind, date(year, 1, 1), date(year, 12, 31))
                for kind in ("movie", "tv") for year in range(FIRST_YEAR, last + 1)
            ], return_exceptions=True)
            errors = [s for s in spans if isinstance(s, BaseException)]
            if len(errors) > len(spans) // 10:
                raise errors[0]
            failed = len(errors)
            rows += [row for span in spans if not isinstance(span, BaseException) for row in span]
        await asyncio.to_thread(self.store, rows, "catalogue", began)
        if full and not failed:
            # Titles the catalogue no longer lists as well known drop out;
            # remembered search results stay.
            with self._connect() as db:
                db.execute("DELETE FROM titles WHERE source='catalogue' AND seen < ?", (began,))
            self._set_meta("full", began)
        elif full:
            log.warning("Title index: %d of %d years failed; trying them again tomorrow.", failed, len(spans))
            self._set_meta("full", began - WEEK + DAY)
        self._set_meta("fresh", began)
        await asyncio.to_thread(self.rebuild)
        log.info("Title index %s: %d titles in %.0fs", "built" if full else "refreshed", self.index.size if self.index else 0, time.time() - began)

    async def tick(self):
        """Do whatever is due: the first build, the daily or weekly refresh, or
        folding remembered titles into the index."""
        if not self.tmdb_key():
            return
        now = time.time()
        if now - self._meta("full") > WEEK:
            await self.refresh(full=True)
        elif now - self._meta("fresh") > DAY:
            await self.refresh(full=False)
        elif self.changed and now - self.built_at > 600:
            await asyncio.to_thread(self.rebuild)

    async def run(self, delay: float = 20):
        if self.count():
            await asyncio.to_thread(self.rebuild)
        await asyncio.sleep(delay)  # let startup settle before fetching
        failures = 0
        while True:
            try:
                await self.tick()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                log.warning("Title index refresh failed (%s); trying again later.", exc)
            # A missing index checks each minute for a TMDB key; failures back off.
            await asyncio.sleep(min(3600, 300 * failures) if failures else 60 if self.index is None else 600)

    def start(self):
        self._task = asyncio.create_task(self.run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._client:
            await self._client.aclose()
            self._client = None


_current: Optional[TitleSearch] = None


def install(search: TitleSearch) -> TitleSearch:
    global _current
    _current = search
    return search


def current() -> Optional[TitleSearch]:
    return _current
