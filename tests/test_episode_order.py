"""Episode order settled in code (issue #3 P5; the 2026-11-22 audit's third
recommendation): when the packs number a season differently from TMDB, the
torrent's real file list is read by TMDB's episode titles, the file carrying
the wanted title is chosen, other copies of the disputed number are marked,
and the mapping is evidence for the reviewer, the Media Agent and the ledger,
so no smart-model search is needed to settle it."""
import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from backend.agents import acquisition_review, ledger, profiles, scout
from backend.agents.models import AgentKind, AgentSession, Event, Job
from backend.agents.node_tools import landing_notes
from backend.agents.nodes import Nodes
from backend.agents.release_match import numbered
from backend.agents.runtime import ToolCtx
from backend.agents.service import AgentService
from backend.models import SparrowConfig
from backend.storage import Storage
from test_bounded_escalation import answer
from test_profiles import environment

GB = 10**9
# A synthetic season in TMDB's order; the packs below use another order, in
# which TMDB's episode 5 is numbered 09 and their 05 is TMDB's episode 9.
SEASON = {1: "The First Light", 2: "Two Roads", 3: "Night Ferry", 4: "Paper Lanterns", 5: "The Lighthouse Keeper",
          6: "Salt and Iron", 7: "Cold Harbour", 8: "Low Tide", 9: "A Quiet Harbour"}
NAMES = {(1, n): t for n, t in SEASON.items()}
PACK = [("Show S01/Show - 04 - Paper Lanterns.mkv", 400), ("Show S01/Show - 05 - A Quiet Harbour.mkv", 410),
        ("Show S01/Show - 09 - The Lighthouse Keeper.mkv", 405), ("Show S01/Extras/Show - 05 NCOP.mkv", 10)]


def indexed(ident, name, seeders, gigabytes, files=1):
    return {"id": str(ident), "name": name, "info_hash": f"{int(ident):040x}", "seeders": str(seeders), "leechers": "0",
            "size": str(int(gigabytes * GB)), "num_files": str(files), "added": "1700000000", "username": "fixture"}


class Index:
    """A toolbox whose indexer answers every search with the same rows."""

    def __init__(self, rows):
        self.rows = rows

    async def index_search(self, query, wait=0, log=None, scope=""):
        return list(self.rows), False


class EpisodeOrderTests(unittest.TestCase):
    def test_a_pack_numbering_the_season_differently_is_read_by_title(self):
        chosen, evidence = scout.match(PACK, [(1, 5)], "tv", ["Show"], (), NAMES, 8)
        self.assertEqual(chosen[(1, 5)]["name"], "Show S01/Show - 09 - The Lighthouse Keeper.mkv")
        self.assertEqual({k: evidence[0][k] for k in ("target", "how", "numbered", "title")},
                         {"target": "S01E05", "how": "title", "numbered": "09", "title": "The Lighthouse Keeper"})
        # By number alone (matching off) the pack's 05 is chosen: another episode.
        by_number, _ = scout.match(PACK, [(1, 5)], "tv", ["Show"], (), NAMES, 0)
        self.assertTrue(by_number[(1, 5)]["name"].endswith("05 - A Quiet Harbour.mkv"))
        # Two wanted episodes the pack swaps: each is found by its own title.
        chosen, evidence = scout.match(PACK, [(1, 5), (1, 9)], "tv", ["Show"], (), NAMES, 8)
        self.assertTrue(chosen[(1, 9)]["name"].endswith("05 - A Quiet Harbour.mkv"))
        self.assertEqual([e["numbered"] for e in evidence], ["09", "05"])
        # A file numbered as the episode whose title says it is another one is not chosen.
        chosen, evidence = scout.match([("Show - S01E05 - A Quiet Harbour.mkv", 410)], [(1, 5)], "tv", ["Show"], (), NAMES, 8)
        self.assertEqual(chosen, {})
        self.assertEqual((evidence[0]["how"], evidence[0]["is_episode"]), ("contradicted", "S01E09"))
        # Its title agreeing with its number, and a name with no title at all.
        _, evidence = scout.match([("Show - S01E05 - The Lighthouse Keeper.mkv", 400)], [(1, 5)], "tv", ["Show"], (), NAMES, 8)
        self.assertEqual(evidence[0]["how"], "title_agrees")
        plain = [("Show.S01E05.1080p.mkv", 400), ("Show.S01E05.1080p.DC.mkv", 500)]
        chosen, evidence = scout.match(plain, [(1, 5)], "tv", ["Show"], (), NAMES, 8)
        self.assertEqual((chosen[(1, 5)]["name"], evidence[0]["how"], evidence[0]["candidates"]),
                         ("Show.S01E05.1080p.mkv", "number", 1))  # the standard cut, as before
        _, evidence = scout.match(plain[:1] + [("Show.S01E05.720p.mkv", 300)], [(1, 5)], "tv", ["Show"], (), NAMES, 8)
        self.assertEqual(evidence[0]["candidates"], 2)

    def test_only_distinctive_titles_are_matched(self):
        names = {(1, 1): "Pilot", (1, 2): "Episode 2", (1, 3): "Show", (1, 4): "Jupiter Jazz",
                 (1, 5): "Jupiter Jazz (Part 1)", (1, 6): "Doug's Dinner", (1, 7): "Toys in the Attic", (2, 4): "Jupiter"}
        found = scout.matchable(names, ["Show"], 8)
        # Too short, "Episode 2", the show's own name, and a title inside another that season are not matched.
        self.assertEqual(set(found), {(1, 5), (1, 6), (1, 7)})
        self.assertTrue(scout._holds(scout.title_words("Show - 06 - Dougs Dinner [1080p].mkv"), found[(1, 6)]))
        self.assertTrue(scout._holds(scout.title_words("Show.S01E12.Jupiter.Jazz.Part.1.mkv"), found[(1, 5)]))
        self.assertEqual(scout.matchable(names, ["Show"], 0), {})
        self.assertEqual(numbered("Show - 09 - The Lighthouse Keeper.mkv"), "09")
        self.assertEqual(numbered("Show (1998) - S01E09 - Title [BD 1080p].mkv"), "S01E09")
        self.assertEqual(numbered("[Group] Show S2 - 05 (1080p).mkv"), "S02E05")
        self.assertEqual(numbered("Show Extras.mkv"), "")

    def test_copies_of_a_disputed_number_are_marked_and_ranked_down(self):
        job = NS(media_type="tv", year=2020, min_quality="720p", preferred_quality="1080p", audio_pref="original",
                 preferences={}, id="job")
        rows = [indexed(1, "Show S01 1080p BluRay", 80, 20, 26), indexed(2, "Show - 05 [1080p]", 200, 1.2),
                indexed(3, "Show - 05 - A Quiet Harbour [1080p]", 10, 1.2)]
        peeks = []
        with patch.object(scout, "listing", AsyncMock(return_value=PACK)):
            plain, *_ = asyncio.run(scout.scout(Index(rows), job, [(1, 5)], ["Show"]))
            ranked, *_ = asyncio.run(scout.scout(Index(rows), job, [(1, 5)], ["Show"], peek_log=peeks, names=NAMES, letters=8))
        self.assertEqual(plain[0]["name"], "Show - 05 [1080p]")  # by number only, the better seeded wrong episode leads
        self.assertEqual(ranked[0]["chosen"], ["Show S01/Show - 09 - The Lighthouse Keeper.mkv"])
        self.assertEqual({r["name"]: r.get("disputed") for r in ranked[1:]},
                         {"Show - 05 [1080p]": ["S01E05"], "Show - 05 - A Quiet Harbour [1080p]": ["S01E05"]})
        text = scout.table(ranked)
        self.assertIn("S01E05 found by its TMDB title as file 09", text)
        self.assertIn("NUMBERING DISPUTED for S01E05", text)
        note = scout.order_note(ranked, peeks)
        self.assertIn('TMDB\'s S01E05 "The Lighthouse Keeper" is numbered 09', note)
        self.assertEqual(peeks[0]["order"][0]["how"], "title")  # the ledger's peek facts keep the evidence

    def test_the_media_agent_is_told_which_episode_a_file_is(self):
        text = landing_notes({"episode_map": [{"file": "Show S01/Show - 09 - The Lighthouse Keeper.mkv", "episode": "S01E05",
                                               "title": "The Lighthouse Keeper", "numbered": "09"}]})
        self.assertIn('"Show - 09 - The Lighthouse Keeper.mkv" is S01E05 "The Lighthouse Keeper"', text)
        self.assertIn("record it as S01E05 if its measured runtime agrees", text)
        self.assertEqual(landing_notes({}), "")

    def test_the_title_length_is_a_versioned_scout_setting(self):
        with environment():
            scouting = profiles.resolve("fetch_scout")
        self.assertEqual(scouting.title_letters, 8)
        self.assertNotIn("title_letters", profiles.resolve("librarian").contents())  # zero: not in its version
        with environment(SPARROW_PROFILES=json.dumps({"fetch_scout": {"title_letters": 0}})):
            off = profiles.resolve("fetch_scout")
        self.assertEqual(off.title_letters, 0)
        self.assertNotEqual(off.version, scouting.version)
        with environment():
            self.assertIn("episode titles of 8+ letters matched in pack files", profiles.table(profiles.all_profiles()))


class GateFixture(unittest.IsolatedAsyncioTestCase):
    """A Fetch Agent on the cheap model with a scripted model, a scripted
    reviewer, a fixed index and listings, and a storage node that records
    adds without a download app."""

    MEDIA = "tv"
    TITLE = "Fixture Show"
    WANTED = {"1": [5]}

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = environment(SPARROW_SMART_MODEL="gpt-6-sol", SPARROW_CHEAP_MODEL="gpt-6-luna")
        self.env.__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.storage = Storage(self.temp.name)
        await self.storage.load_all()
        await self.storage.save_config(SparrowConfig(tmdb_api_key="fixture-not-sent"))
        self.service = AgentService(self.storage, self.temp.name, AsyncMock())
        self.runtime, self.tb = self.service.runtime, self.service.toolbox
        self.tb.SEARCH_SPACING = 0
        self.tb.select_soon = AsyncMock()
        self.namesakes = []
        self.tb.tmdb_get = self.tmdb
        self.job = Job(tmdb_id=42, media_type=self.MEDIA, title=self.TITLE, year=2020, original_language="en",
                       audio_pref="original", wanted_episodes=self.WANTED if self.MEDIA == "tv" else {})
        self.session = AgentSession(agent=AgentKind.FETCH, job_id=self.job.id, model="gpt-6-luna")
        self.job.session_id = self.session.id
        self.service.store.save_job(self.job)
        self.service.store.save_session(self.session)
        self.rows, self.listings, self.added = [], {}, []
        self.verdicts = []
        self.reviewer = AsyncMock(side_effect=lambda *a, **k: self.verdicts.pop(0))
        for patcher in (patch("backend.agents.tools.apibay_query", AsyncMock(side_effect=self.index)),
                        patch.object(scout, "listing", self.listing), patch.object(Nodes, "execute", self.execute),
                        patch.object(acquisition_review, "review", self.reviewer)):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def tmdb(self, path, **params):
        if path.endswith("/alternative_titles"):
            return {"results": [], "titles": []}
        if path.startswith("/search/"):
            return {"results": [{"id": 42, "name": self.TITLE, "title": self.TITLE}, *self.namesakes]}
        if "/season/" in path:
            return {"episodes": [{"episode_number": n, "name": t, "runtime": 24} for n, t in SEASON.items()]}
        return {"id": 42, "name": self.TITLE, "title": self.TITLE, "original_language": "en"}

    async def index(self, query, cat="0", *, strict=False):
        return [dict(r) for r in self.rows]

    async def listing(self, apibay_id):
        return list(self.listings.get(str(apibay_id), []))

    async def execute(self, nodes, node_id, kind, args=None, *, operation_id=None, job=None, timeout=60):
        if kind == "download_add":
            self.added.append(dict(args or {}))
            return {"hash": (args or {}).get("info_hash", "")}
        return {"status": "downloading", "progress": 0.0}

    def verdict(self, approve=True, reason="It fits.", concern="none", instead="", queries=()):
        self.verdicts.append(({"approve": approve, "reason": reason, "instead": instead, "queries": list(queries),
                               "concern": concern}, 0.002, "gpt-6-sol", {"input_tokens": 600, "output_tokens": 40}))

    async def run_turn(self, *calls):
        replies = iter([answer(call, tokens=800) for call in calls] + [answer(("wake_me", {"reason": "Waiting."}), tokens=800)])
        self.runtime._call_api = AsyncMock(side_effect=lambda *_: next(replies))
        await self.runtime.wake(self.session.id, Event(kind="job_created", payload={"description": "New request."}))

    def downloads(self):
        return [d for d in self.storage.get_all_downloads() if d.metadata.get("job_id") == self.job.id]

    def tool(self, name):
        return next(t for t in self.runtime.tools_for(self.session) if t.name == name).handler

    def stock_disputed_season(self):
        """A pack numbering TMDB's episode 5 as 09, and a better-seeded single numbered 05."""
        self.rows = [indexed(11, "Fixture Show S01 1080p BluRay", 80, 20, 26), indexed(12, "Fixture Show - 05 [1080p]", 200, 1.2)]
        self.listings = {"11": [(f"Fixture Show S01/Fixture Show - {n} - {t}.mkv", 400 * 10**6)
                                for n, t in (("04", "Paper Lanterns"), ("05", "A Quiet Harbour"), ("09", "The Lighthouse Keeper"))]}


class EpisodeOrderGateTests(GateFixture):
    async def test_a_season_numbered_differently_is_settled_without_a_search(self):
        self.stock_disputed_season()
        self.verdict(reason="The chosen file is the requested episode by its title.")
        await self.run_turn(("find_releases", {}), ("propose_release", {"release": "r1", "reason": "the pack's file by title"}))

        self.assertEqual(self.reviewer.await_count, 1)
        args = self.reviewer.await_args.args
        rows, pick, note = args[3], args[4], args[8]
        self.assertIn('TMDB\'s S01E05 "The Lighthouse Keeper" is numbered 09', note)
        record = acquisition_review.record(self.job, args[2], rows, pick, args[5], args[6], args[7], note)
        self.assertIn("chosen file: Fixture Show S01/Fixture Show - 09 - The Lighthouse Keeper.mkv", record)
        self.assertIn("NUMBERING DISPUTED for S01E05", record)  # the better-seeded single numbered 05
        [download] = self.downloads()
        self.assertEqual(download.metadata["wanted_files"], ["Fixture Show S01/Fixture Show - 09 - The Lighthouse Keeper.mkv"])
        self.assertEqual(download.metadata["episode_map"][0]["episode"], "S01E05")
        self.assertIn("record it as S01E05", landing_notes(download.metadata))
        # No escalation, and the mapping is ledger evidence.
        self.assertEqual(self.service.store.get_session(self.session.id).model, "gpt-6-luna")
        order = next(r for r in ledger.load_enforcements(self.service.store) if r["rule"] == "episode_order")
        self.assertEqual((order["profile"], order["tool"]), ("fetch_scout", "find_releases"))
        self.assertEqual(order["detail"]["mapped"][0]["numbered"], "09")
        self.assertEqual(order["detail"]["disputed"], ["r2"])
        self.assertFalse(ledger.summarize(self.service.store)["titles"][0]["escalations"])

    async def test_a_copy_added_by_hand_keeps_codes_episode_evidence(self):
        from backend.agents.node_tools import acquisition_tools

        self.rows = [indexed(11, "Fixture Show S01 1080p BluRay", 80, 20, 26)]
        self.listings = {"11": [("Fixture Show S01/Fixture Show - 09 - The Lighthouse Keeper.mkv", 400 * 10**6)]}
        ctx = ToolCtx(self.session, self.runtime)
        await self.tool("find_releases")(ctx, {})
        client_add = next(t for t in acquisition_tools(self.tb) if t.name == "client_add").handler
        await client_add(ctx, {"info_hash": f"{11:040x}", "name": "Fixture Show S01 1080p BluRay",
                               "files": ["Fixture Show S01/Fixture Show - 09 - The Lighthouse Keeper.mkv"]})
        [download] = self.downloads()
        self.assertEqual([(m["episode"], m["numbered"]) for m in download.metadata["episode_map"]], [("S01E05", "09")])


if __name__ == "__main__":
    unittest.main()
