"""Find's title index: matching typed text to the title meant, and keeping the
stored copy of the catalogue built and fresh by itself."""

import asyncio
import json
import math
import os
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from backend.agents import title_index
from backend.agents.title_index import TitleIndex, TitleSearch, allowance, plain, show_words
from backend.storage import Storage
from backend.models import SparrowConfig

# A slice of the real catalogue: its best-known titles and the near misses
# that compete with them (Dark Night, The Officers' Ward, Interstella 5555…).
FIXTURE = Path(__file__).parent / "fixtures" / "titles.jsonl"
ROWS = [tuple(json.loads(line)) for line in FIXTURE.open(encoding="utf-8")]
INDEX = TitleIndex(ROWS)
NAMES = {(r[0], r[1]): (r[2], r[4]) for r in ROWS}


def top(query, n=1):
    found, _ = INDEX.search(query)
    return [NAMES[INDEX.key(i)][0] for i, _ in found[:n]]


class MatchingTests(unittest.TestCase):
    def test_typed_text_finds_the_title_meant(self):
        cases = {
            # partial words while typing
            "gla": "Gladiator",
            "pulp fic": "Pulp Fiction",
            "the off": "The Office",
            "game of throne": "Game of Thrones",
            "breakin": "Breaking Bad",
            # typos
            "gladiater": "Gladiator",
            "brekaing bad": "Breaking Bad",
            "lord of the rigns": "The Lord of the Rings: The Fellowship of the Ring",
            "shawshank redemtion": "The Shawshank Redemption",
            "intersteller": "Interstellar",
            "intstellar": "Interstellar",
            "the ofice": "The Office",
            "dark night": "The Dark Knight",
            "the matrx": "The Matrix",
            "oppenhimer": "Oppenheimer",
            "squid gaem": "Squid Game",
            "jhon wick": "John Wick",
            "seinfield": "Seinfeld",
            # spacing, punctuation and accents
            "spider man": "Spider-Man",
            "spiderman": "Spider-Man",
            "starwars": "Star Wars",
            "walle": "WALL·E",
            "amelie": "Amélie",
            # numbers written any way
            "godfather 2": "The Godfather Part II",
            "godfather ii": "The Godfather Part II",
            "dune part 2": "Dune: Part Two",
            "toy story two": "Toy Story 2",
            # seasons and episodes search for the show
            "breaking bad season 2": "Breaking Bad",
            "the office s03e04": "The Office",
            # short titles
            "it": "It",
            "her": "Her",
            "up": "Up",
        }
        for query, title in cases.items():
            with self.subTest(query=query):
                self.assertEqual(top(query), [title])

    def test_the_lists_the_best_known_titles(self):
        self.assertEqual(
            top("the", 3), ["The Shawshank Redemption", "The Dark Knight", "The Godfather"]
        )
        self.assertEqual(top("t", 3), top("the", 3))
        found, _ = INDEX.search("the")
        self.assertEqual(len(found), title_index.RESULTS)

    def test_a_year_picks_the_version(self):
        found, _ = INDEX.search("dune 2021")
        self.assertEqual(NAMES[INDEX.key(found[0][0])], ("Dune", "2021"))

    def test_typos_are_counted(self):
        exact, _ = INDEX.search("gladiator")
        typo, _ = INDEX.search("gladiater")
        self.assertEqual((exact[0][0], exact[0][1]), (typo[0][0], 0))
        self.assertEqual(typo[0][1], 1)

    def test_descriptions_and_noise_find_nothing(self):
        for query in ("a funny film under two hours", "the zzzz", "zzqx vrrb", "   "):
            with self.subTest(query=query):
                self.assertEqual(INDEX.search(query)[0], [])

    def test_words(self):
        self.assertEqual(plain("Spider-Man: Into the Spider-Verse"), "spider man into the spider verse")
        self.assertEqual(plain("Amélie & Schindler’s List"), "amelie and schindlers list")
        self.assertEqual(show_words(["breaking", "bad", "season", "two"]), ["breaking", "bad"])
        self.assertEqual(show_words(["the", "office", "s03e04"]), ["the", "office"])
        self.assertEqual(show_words(["season"]), ["season"])
        self.assertEqual([allowance(w) for w in ("her", "dune", "gladiate", "2021")], [0, 1, 2, 0])

    def test_searching_is_fast(self):
        queries = ["gladiater", "lord of the rigns", "the g", "spiderman", "t"]
        began = time.perf_counter()
        for _ in range(5):
            for query in queries:
                INDEX._expansions.clear()
                INDEX._shorts.clear()
                INDEX.search(query)
        self.assertLess((time.perf_counter() - began) / 25, 0.05)


def release(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


class FakeCatalogue:
    """Just enough of TMDB's discover, trending and search for the index."""

    def __init__(self, titles, per_page=20):
        self.titles = titles
        self.per_page = per_page
        self.calls = []
        self.broken = lambda path, params: False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/3")
        params = dict(request.url.params)
        self.calls.append((path, params))
        if self.broken(path, params):
            return httpx.Response(500)
        if path.startswith("/discover/"):
            kind = path.rsplit("/", 1)[1]
            field = "primary_release_date" if kind == "movie" else "first_air_date"
            pool = [
                t for t in self.titles
                if t["kind"] == kind and params[f"{field}.gte"] <= t["date"] <= params[f"{field}.lte"]
                and t["votes"] >= int(params.get("vote_count.gte", 0))
            ]
        elif path.startswith("/trending/"):
            pool = [t for t in self.titles if t["kind"] == path.split("/")[2] and t.get("trending")]
        elif path == "/search/multi":
            pool = [t for t in self.titles if params["query"].lower() in t["title"].lower()]
        else:
            return httpx.Response(404)
        page = int(params.get("page", 1))
        chunk = pool[(page - 1) * self.per_page : page * self.per_page]
        return httpx.Response(200, json={
            "page": page,
            "total_pages": max(1, math.ceil(len(pool) / self.per_page)),
            "results": [self.result(t) for t in chunk],
        })

    @staticmethod
    def result(t):
        out = {"id": t["id"], "media_type": t["kind"], "popularity": t.get("popularity", 5),
               "vote_count": t["votes"], "vote_average": 7.0, "poster_path": f"/{t['id']}.jpg"}
        if t["kind"] == "movie":
            out.update(title=t["title"], original_title=t["title"], release_date=t["date"])
        else:
            out.update(name=t["title"], original_name=t["title"], first_air_date=t["date"])
        return out

    def requests(self, prefix):
        return [c for c in self.calls if c[0].startswith(prefix)]


def catalogue_titles():
    return [
        {"kind": "movie", "id": 1, "title": "Harbour Lights", "date": "2019-03-01", "votes": 900},
        {"kind": "movie", "id": 2, "title": "The Quiet Planet", "date": "2019-06-01", "votes": 400},
        {"kind": "movie", "id": 3, "title": "Tomorrow Bay", "date": "2019-09-01", "votes": 50},
        {"kind": "movie", "id": 4, "title": "Northern Signal", "date": "2019-11-01", "votes": 30},
        {"kind": "movie", "id": 5, "title": "A Long Way Home", "date": "2020-02-01", "votes": 20},
        {"kind": "movie", "id": 6, "title": "Barely Seen", "date": "2020-02-01", "votes": 2},
        {"kind": "tv", "id": 7, "title": "The Last Evening", "date": "2020-05-01", "votes": 60},
    ]


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.key = "fixture-key"
        self.catalogue = FakeCatalogue(catalogue_titles(), per_page=1)
        self.search = TitleSearch(self.temp.name, lambda: self.key, transport=httpx.MockTransport(self.catalogue))
        for name, value in (("FIRST_YEAR", 2019), ("MAX_PAGES", 2), ("RETRY_DELAY", 0)):
            patcher = patch.object(title_index, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.search.stop()
        self.temp.cleanup()

    def stored(self):
        with self.search._connect() as db:
            return {(r["media_type"], r["tmdb_id"]): r["source"] for r in db.execute("SELECT * FROM titles")}

    async def test_nothing_happens_without_a_tmdb_key(self):
        self.key = ""
        await self.search.tick()
        self.assertEqual(self.catalogue.calls, [])
        self.assertEqual((await self.search.suggest("quiet"))["more"], True)

    async def test_first_build_lists_every_well_known_title(self):
        await self.search.tick()
        # Films need ten votes; a full year (over MAX_PAGES pages) was halved.
        self.assertEqual(set(self.stored()), {("movie", n) for n in (1, 2, 3, 4, 5)} | {("tv", 7)})
        spans = {(p["primary_release_date.gte"], p["primary_release_date.lte"])
                 for _, p in self.catalogue.requests("/discover/movie") if "vote_count.gte" in p}
        self.assertIn(("2019-01-01", "2019-07-02"), spans)
        result = await self.search.suggest("quiet plnet")
        self.assertEqual([t["title"] for t in result["titles"]], ["The Quiet Planet"])
        self.assertEqual(result["titles"][0]["poster_url"], "https://image.tmdb.org/t/p/w185/2.jpg")
        # Nothing is due again until tomorrow.
        calls = len(self.catalogue.calls)
        await self.search.tick()
        self.assertEqual(len(self.catalogue.calls), calls)

    async def test_daily_refresh_adds_new_and_upcoming_titles(self):
        await self.search.tick()
        self.catalogue.titles.append(
            {"kind": "movie", "id": 8, "title": "Comet Season", "date": release(90), "votes": 0, "popularity": 80}
        )
        self.search._set_meta("fresh", time.time() - title_index.DAY - 1)
        self.catalogue.calls.clear()
        await self.search.tick()
        self.assertFalse(any("vote_count.gte" in p for _, p in self.catalogue.calls))
        self.assertEqual([t["title"] for t in (await self.search.suggest("comet"))["titles"]], ["Comet Season"])

    async def test_weekly_refresh_drops_unlisted_titles_but_keeps_remembered_ones(self):
        await self.search.tick()
        self.catalogue.titles = [t for t in self.catalogue.titles if t["id"] != 4]
        self.search.store([("movie", 99, "Kummatty", "", "1979", 1.0, 3, 7.0, "")], "search")
        self.search._set_meta("full", time.time() - title_index.WEEK - 1)
        await self.search.tick()
        stored = self.stored()
        self.assertNotIn(("movie", 4), stored)
        self.assertEqual(stored[("movie", 99)], "search")
        self.assertEqual([t["title"] for t in (await self.search.suggest("kummaty"))["titles"]], ["Kummatty"])

    async def test_a_failing_year_keeps_the_rest_and_tries_again_tomorrow(self):
        self.catalogue.broken = lambda path, params: params.get("first_air_date.gte", "").startswith("2020")
        began = time.time()
        await self.search.tick()
        self.assertIn(("movie", 1), self.stored())
        self.assertNotIn(("tv", 7), self.stored())
        retry = self.search._meta("full") + title_index.WEEK
        self.assertAlmostEqual(retry, began + title_index.DAY, delta=5)

    async def test_live_search_fills_gaps_and_is_remembered(self):
        await self.search.tick()
        self.catalogue.titles.append({"kind": "movie", "id": 9, "title": "Kummatty", "date": "1979-01-01", "votes": 3})
        narrow = await self.search.suggest("kummatty")
        self.assertEqual((narrow["titles"], narrow["more"]), ([], True))
        wide = await self.search.suggest("kummatty", wide=True)
        self.assertEqual(([t["title"] for t in wide["titles"]], wide["more"]), (["Kummatty"], False))
        self.assertEqual(self.stored()[("movie", 9)], "search")
        searches = len(self.catalogue.requests("/search/multi"))
        await self.search.suggest("kummatty", wide=True)
        self.assertEqual(len(self.catalogue.requests("/search/multi")), searches)
        await asyncio.to_thread(self.search.rebuild)
        self.assertEqual([t["title"] for t in (await self.search.suggest("kumatty"))["titles"]], ["Kummatty"])

    async def test_clean_matches_need_no_live_search(self):
        self.catalogue.titles += [
            {"kind": "movie", "id": 10, "title": "The Night Ferry", "date": "2019-04-01", "votes": 100},
            {"kind": "movie", "id": 11, "title": "The Long Field", "date": "2020-04-01", "votes": 100},
        ]
        await self.search.tick()
        searches = len(self.catalogue.requests("/search/multi"))
        result = await self.search.suggest("the", wide=True)
        self.assertEqual(len(self.catalogue.requests("/search/multi")), searches)
        self.assertFalse(result["more"])

    async def test_a_failing_live_search_is_quiet(self):
        await self.search.tick()
        self.catalogue.broken = lambda path, params: path == "/search/multi"
        result = await self.search.suggest("quiet plnet", wide=True)
        self.assertEqual([t["title"] for t in result["titles"]], ["The Quiet Planet"])

    async def test_the_stored_copy_loads_after_a_restart(self):
        await self.search.tick()
        again = TitleSearch(self.temp.name, lambda: "")
        await asyncio.to_thread(again.rebuild)
        self.assertEqual(again.index.size, 6)
        self.assertEqual([t["title"] for t in (await again.suggest("harbour"))["titles"]], ["Harbour Lights"])


class SuggestRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from backend.agents.account_api import COOKIE, install_accounts
        from backend.agents.nodes import install_nodes
        from backend.agents.product_api import install_product

        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(self.temp.name)
        await self.storage.load_all()
        app = FastAPI()
        accounts = install_accounts(app, self.storage, lambda: None)
        owner = accounts.create_user("owner", "fixture-password-123", "Owner", bootstrap=True)
        nodes = install_nodes(app, self.storage, lambda: None)
        install_product(app, self.storage, accounts, nodes, lambda: None)
        self.titles = title_index.current()
        self.titles.store([("movie", 278, "The Shawshank Redemption", "", "1994", 50.0, 30000, 8.7, "/s.jpg")])
        self.titles.rebuild()
        self.titles.live = AsyncMock(return_value=[])
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Sparrow-Request": "1"},
            cookies={COOKIE: accounts.new_session(owner["id"])},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def test_needs_a_tmdb_key(self):
        with patch.dict(os.environ, {"TMDB_API_KEY": ""}):
            response = await self.client.get("/api/v1/suggest", params={"q": "shawshank"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("Connect TMDB", response.json()["detail"])

    async def test_one_letter_finds_titles(self):
        await self.storage.save_config(SparrowConfig(tmdb_api_key="fixture-not-sent"))
        response = await self.client.get("/api/v1/suggest", params={"q": "s"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "titles": [{"tmdb_id": 278, "media_type": "movie", "title": "The Shawshank Redemption",
                        "year": "1994", "poster_url": "https://image.tmdb.org/t/p/w185/s.jpg", "rating": 8.7}],
            "more": True,
        })
        wide = await self.client.get("/api/v1/suggest", params={"q": "shawshank redemtion", "wide": "1"})
        self.assertEqual(wide.json()["titles"][0]["tmdb_id"], 278)
        self.titles.live.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
