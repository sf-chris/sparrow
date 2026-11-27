"""Found: titles already in a library folder, named from facts and added by themselves when sure."""

import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from backend.agents.found import Found, guess, summary
from backend.agents.media_state import file_version
from backend.agents.tools import ToolError
from test_moves import MoveFixture

FILMS = {
    "arrival": [
        {"id": 329865, "title": "Arrival", "original_title": "Arrival", "release_date": "2016-11-10"},
        {"id": 12345, "title": "The Arrival", "original_title": "The Arrival", "release_date": "1996-05-31"},
    ],
    "heat": [
        {"id": 949, "title": "Heat", "original_title": "Heat", "release_date": "1995-12-15"},
        {"id": 950, "title": "Heat", "original_title": "Heat", "release_date": "1986-03-14"},
    ],
}
DETAILS = {
    "/movie/329865": {"id": 329865, "title": "Arrival", "runtime": 116, "release_date": "2016-11-10"},
    "/movie/949": {"id": 949, "title": "Heat", "runtime": 170, "release_date": "1995-12-15"},
    "/tv/95396": {"id": 95396, "name": "Severance", "first_air_date": "2022-02-17"},
}
MINUTES = {"Arrival.mkv": 117, "S01E01.mkv": 57, "Heat.mkv": 95, "Mystery.mkv": 100}


class FoundTests(MoveFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.calls = []
        self.tmdb_down = False

        async def tmdb_get(path, **params):
            self.calls.append(path)
            if self.tmdb_down:
                raise ToolError("TMDB unreachable")
            if path == "/search/movie":
                return {"results": FILMS.get(params["query"].lower(), [])}
            if path == "/search/tv":
                return {"results": [{"id": 95396, "name": "Severance", "original_name": "Severance",
                                     "first_air_date": "2022-02-17"}] if params["query"] == "Severance" else []}
            if path == "/tv/95396/season/1":
                return {"episodes": [{"episode_number": 1, "runtime": 57}]}
            return DETAILS[path]

        self.service = SimpleNamespace(
            store=SimpleNamespace(get_jobs=lambda: self.jobs), toolbox=SimpleNamespace(tmdb_get=tmdb_get)
        )
        self.found = Found(self.accounts, self.nodes, self.storage, self.catalogue, lambda: self.service)
        library = self.root / "one"
        for relative in (
            "Films/Arrival (2016)/Arrival.mkv",
            "TV/Severance/Season 1/S01E01.mkv",
            "Films/Heat (1995)/Heat.mkv",
            "Films/Mystery.mkv",
            "Films/Arrival (2016)/Featurettes/Making of.mkv",
        ):
            path = library / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"video " + relative.encode())

        async def probe(path):
            version = file_version(path)
            return {
                "version": version, "size_bytes": version["size_bytes"],
                "duration": MINUTES.get(path.name, 90) * 60.0, "quality": "1080p",
                "audio_tracks": [], "audio_languages": [], "subtitle_tracks": [],
            }

        self.probe = mock.patch("backend.agents.node_executor.probe_file", probe)
        self.probe.start()
        self.settle = mock.patch("backend.agents.found.SETTLE", 0.0)

    async def asyncTearDown(self):
        self.probe.stop()
        await super().asyncTearDown()

    def state(self, relative):
        with self.accounts.connect() as db:
            row = db.execute(
                "SELECT data FROM found_files WHERE node_id='local' AND root_id='library' AND path=?",
                (relative,),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    async def look(self):
        self.found.request("local")
        await self.found.run_pending()

    async def test_files_are_named_once_settled_and_added_only_when_the_facts_agree(self):
        await self.look()
        # Seen once: nothing is named until it has settled.
        self.assertEqual(self.state("Films/Arrival (2016)/Arrival.mkv")["state"], "new")
        self.assertNotIn("/search/movie", self.calls)
        self.assertIsNone(self.state("Fixture show/Season 01/S01E01.mkv"))  # Already in the collection.
        with self.settle:
            await self.look()
        arrival = self.state("Films/Arrival (2016)/Arrival.mkv")
        self.assertEqual((arrival["state"], arrival["added"]["title"]), ("added", "Arrival"))
        self.assertTrue(arrival["added"]["automatic"])
        self.assertIsNotNone(self.catalogue.item_for("local", "movie", 329865))
        self.assertEqual(self.state("TV/Severance/Season 1/S01E01.mkv")["state"], "added")
        severance = self.catalogue.item_for("local", "tv", 95396)
        self.assertEqual(severance.title, "Severance")
        # Heat (1995) is a film, but this file is 95 minutes and that one 170.
        heat = self.state("Films/Heat (1995)/Heat.mkv")
        self.assertEqual(heat["state"], "unsure")
        self.assertEqual(heat["reason"], "It's 95 minutes, and Heat is 170.")
        mystery = self.state("Films/Mystery.mkv")
        self.assertEqual((mystery["state"], mystery["reason"]), ("unsure", "No film is called Mystery."))
        self.assertEqual(self.state("Films/Arrival (2016)/Featurettes/Making of.mkv")["state"], "ignored")
        report = summary(self.accounts, "local")
        self.assertEqual((report["added"], report["unsure"], report["looking"]), (2, 2, False))
        self.assertGreater(report["checked"], 0)
        # Files stay where they are.
        self.assertTrue((self.root / "one" / "Films/Arrival (2016)/Arrival.mkv").is_file())

        # A person names one and sets the other aside.
        result = await self.found.add(
            "local", [{"root_id": "library", "path": "Films/Heat (1995)/Heat.mkv", "tmdb_id": 949, "media_type": "movie"}]
        )
        self.assertEqual([a["title"] for a in result["added"]], ["Heat"])
        self.found.ignore("local", [{"root_id": "library", "path": "Films/Mystery.mkv"}])
        self.assertEqual(summary(self.accounts, "local")["unsure"], 0)
        listing = self.found.files("local")
        self.assertEqual(listing["unsure"], [])
        self.assertEqual({a["title"] for a in listing["added"]}, {"Arrival", "Severance", "Heat"})

        # Looking again changes nothing it has settled.
        self.calls.clear()
        with self.settle:
            await self.look()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state("Films/Mystery.mkv")["state"], "ignored")

    async def test_a_file_still_arriving_waits(self):
        growing = self.root / "one" / "Films/Mystery.mkv"
        await self.look()
        growing.write_bytes(b"more of the copy arrived")
        with self.settle:
            await self.look()
        # It changed since it was first seen, so it starts settling again.
        self.assertEqual(self.state("Films/Mystery.mkv")["state"], "new")

    async def test_nothing_is_named_while_tmdb_is_away(self):
        await self.look()
        self.tmdb_down = True
        with self.settle:
            await self.look()
        self.assertEqual(self.state("Films/Arrival (2016)/Arrival.mkv")["state"], "new")
        self.assertIn("TMDB didn't answer", summary(self.accounts, "local")["error"])
        self.tmdb_down = False
        with self.settle:
            await self.look()
        self.assertEqual(self.state("Films/Arrival (2016)/Arrival.mkv")["state"], "added")
        self.assertEqual(summary(self.accounts, "local")["error"], "")

    async def test_a_title_being_moved_in_is_not_found(self):
        with self.accounts.connect() as db:
            db.execute(
                "INSERT INTO storage_moves VALUES ('m', 'item', 'copying', ?, ?, ?)",
                (
                    json.dumps({"to_node": "local", "to_root": "library",
                                "assets": [{"files": [{"path": "Films/Mystery.mkv"}]}]}),
                    time.time(),
                    time.time(),
                ),
            )
        await self.look()
        self.assertIsNone(self.state("Films/Mystery.mkv"))
        self.assertIsNotNone(self.state("Films/Heat (1995)/Heat.mkv"))


class FoundApiTests(FoundTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        import httpx
        from fastapi import FastAPI

        from backend.agents.found import install_found

        app = FastAPI()

        @app.middleware("http")
        async def administrator(request, call_next):
            request.state.user = self.owner
            return await call_next(request)

        self.found = install_found(app, self.accounts, self.nodes, self.storage, self.catalogue, lambda: self.service)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await super().asyncTearDown()

    async def test_files_are_named_and_set_aside_by_their_own_routes(self):
        looked = await self.client.post("/api/v1/admin/found/local/look")
        self.assertTrue(looked.json()["looking"])
        await self.found.run_pending()
        with self.settle:
            await self.look()
        listing = (await self.client.get("/api/v1/admin/found/local")).json()
        self.assertEqual(listing["summary"]["unsure"], 2)
        heat = "Films/Heat (1995)/Heat.mkv"
        waiting = {f["path"]: f for f in listing["unsure"]}
        self.assertEqual(waiting[heat]["guess"]["title"], "Heat")
        self.assertEqual(waiting[heat]["reason"], "It's 95 minutes, and Heat is 170.")
        bad = await self.client.post(
            "/api/v1/admin/found/local/add",
            json={"files": [{"root_id": "library", "path": heat, "tmdb_id": 95396, "media_type": "tv"}]},
        )
        self.assertEqual(bad.status_code, 422)
        added = await self.client.post(
            "/api/v1/admin/found/local/add",
            json={"files": [{"root_id": "library", "path": heat, "tmdb_id": 949, "media_type": "movie"}]},
        )
        self.assertEqual([a["title"] for a in added.json()["added"]], ["Heat"])
        aside = await self.client.post(
            "/api/v1/admin/found/local/ignore",
            json={"files": [{"root_id": "library", "path": "Films/Mystery.mkv"}]},
        )
        self.assertEqual((aside.json()["unsure"], aside.json()["summary"]["unsure"]), ([], 0))
        self.assertEqual((await self.client.get("/api/v1/admin/found/nowhere")).status_code, 404)


class GuessTests(unittest.TestCase):
    def test_names_and_folders(self):
        cases = {
            "The.Matrix.1999.1080p.BluRay.x264.mkv": ("movie", "The Matrix", 1999, None, None),
            "Films/Arrival (2016)/Arrival.mkv": ("movie", "Arrival", 2016, None, None),
            "Blade.Runner.2049.2017.1080p.mkv": ("movie", "Blade Runner 2049", 2017, None, None),
            "2001 A Space Odyssey (1968).mkv": ("movie", "2001 A Space Odyssey", 1968, None, None),
            "TV/Severance/Season 02/Severance - S02E03 - Who Is Alive.mkv": ("tv", "Severance", None, 2, 3),
            "Severance/Season 1/S01E01.mkv": ("tv", "Severance", None, 1, 1),
            "The Office/Season 2/01 - The Dundies.mkv": ("tv", "The Office", None, 2, 1),
            "Doctor Who (2005)/Season 1/Doctor.Who.2005.S01E01.mkv": ("tv", "Doctor Who", 2005, 1, 1),
        }
        for path, expected in cases.items():
            g = guess(path)
            self.assertEqual((g["media_type"], g["title"], g["year"], g["season"], g["episode"]), expected, path)
        for extra in ("sample.mkv", "Films/Dune (2021)/Featurettes/Making of.mkv", "Show/Extras/Gag reel.mkv"):
            self.assertIsNone(guess(extra), extra)


if __name__ == "__main__":
    unittest.main()
