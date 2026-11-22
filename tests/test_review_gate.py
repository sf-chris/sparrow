"""The pick review made conditional, without losing its safeguards (issue #3
P5; the 2026-11-22 audit's fourth and second recommendations).

The smart-model review is skipped only for picks code verified on every point
it checks; anything uncertain keeps it. A veto only for quality or audio the
name cannot show downloads the copy once to be measured, instead of a hard
veto and a smart-model search; the Media Agent's checks stay authoritative.
Every decision is a ledger row with the fetch_review profile that decided it."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from backend.agents import acquisition_review, ledger, profiles
from backend.agents.acquisition_review import ATTRIBUTES, FILES, IDENTITY, RANKING, SWARM
from backend.agents.node_tools import landing_notes
from backend.agents.runtime import ToolCtx
from test_episode_order import GB, GateFixture, indexed
from test_profiles import environment


class ReviewDoubtTests(unittest.TestCase):
    def setUp(self):
        self.tv = NS(media_type="tv", year=2020, title="Show", audio_pref="original", original_language="en",
                     preferences={"values": {}, "policy": {}})
        self.row = {"rid": "r1", "name": "Show S01 1080p BluRay", "coverage": "pack", "quality": "1080p", "seeders": 60,
                    "dual": False, "subs": False, "raw": False, "year": None, "other_part": False,
                    "chosen": ["Show S01/Show - S01E02 - Two Roads.mkv"], "covers": [[1, 2]],
                    "matched": [{"target": "S01E02", "how": "title_agrees", "candidates": 1}]}

    def kinds(self, row, rows=None, job=None, **kwargs):
        return [kind for kind, _ in acquisition_review.doubts(job or self.tv, row, rows or [row], targets=[(1, 2)], **kwargs)]

    def test_a_pick_verified_on_every_point_has_no_doubt(self):
        self.assertEqual(self.kinds(self.row), [])

    def test_anything_uncertain_keeps_the_review(self):
        row = self.row
        cases = {
            "namesake": (self.kinds(row, namesakes="Show (2009, original language en)"), [IDENTITY]),
            "unlisted pack": (self.kinds({**row, "unlisted": True, "chosen": ["episode:S01E02"]}), [FILES]),
            "several files": (self.kinds({**row, "matched": [{"how": "number", "candidates": 2}]}), [FILES]),
            "found under another number": (self.kinds({**row, "matched": [{"how": "title", "candidates": 1}]}), [IDENTITY]),
            "disputed": (self.kinds({**row, "disputed": ["S01E02"]}), [IDENTITY]),
            "another season or cut": (self.kinds({**row, "other_part": True}), [IDENTITY]),
            "unknown quality": (self.kinds({**row, "quality": "unknown"}), [ATTRIBUTES]),
            "raw": (self.kinds({**row, "raw": True}), [ATTRIBUTES]),
            "few seeds": (self.kinds({**row, "seeders": 4}), [SWARM]),
            "not code's first": (self.kinds({**row, "rid": "r2"}, rows=[{**row, "rid": "r1"}, {**row, "rid": "r2"}]), [RANKING]),
            "abandoned": (self.kinds({**row, "abandoned": True}), [IDENTITY]),
        }
        for name, (found, expected) in cases.items():
            self.assertEqual(found, expected, name)
        anime = NS(**{**vars(self.tv), "original_language": "ja"})
        self.assertEqual(self.kinds(row, job=anime), [ATTRIBUTES])  # original audio not named
        self.assertEqual(self.kinds({**row, "dual": True}, job=anime), [])
        strict = NS(**{**vars(anime), "preferences": {"values": {"require_subtitles": True}, "policy": {"max_quality": "720p"}}})
        found = acquisition_review.doubts(strict, {**row, "dual": True}, [row], targets=[(1, 2)])
        self.assertEqual([words for _, words in found],
                         ["its quality is above the household's limit", "its name does not show the subtitles the request requires"])
        dubbed = acquisition_review.doubts(self.tv, {**row, "name": "Show S01 1080p FRENCH"}, [row], targets=[(1, 2)])
        self.assertEqual([words for _, words in dubbed], ["a dubbed language is named"])

    def test_a_single_release_is_verified_from_its_real_file_list(self):
        film = NS(media_type="movie", year=2020, title="Film", audio_pref="original", original_language="en",
                  preferences={"values": {}, "policy": {}})
        row = {"rid": "r1", "name": "Film.2020.1080p.WEB-DL", "coverage": "single", "quality": "1080p", "seeders": 50,
               "dual": False, "year": 2020, "size": 2 * GB}

        def doubts(**kwargs):
            return [words for _, words in acquisition_review.doubts(film, row, [row], titles=["Film"], **kwargs)]

        self.assertEqual(doubts(), ["its file list was not read"])
        self.assertEqual(doubts(listing=[("Film.2020.1080p.WEB-DL.mkv", 2 * GB), ("Film.2020.nfo", 100)]), [])
        self.assertEqual(doubts(listing=[("Film.2020.1080p.WEB-DL.mkv", 2 * GB), ("Codec.exe", 1000)]),
                         ["its file list holds programs or archives"])
        self.assertEqual(doubts(listing=[("Film.2020.Part1.mkv", GB), ("Film.2020.Part2.mkv", GB)]),
                         ["its file list holds 2 videos"])
        self.assertEqual(doubts(listing=[("abc123.mkv", 2 * GB)]), ["its video file is not named for the title"])
        self.assertEqual(doubts(listing=[("Film.2020/Film.2020.1080p.mkv", 2 * GB), ("Film.2020/Extras/Trailer.mkv", GB // 10),
                                         ("Film.2020/Film.2020.sample.mkv", GB // 20)]), [])  # extras and samples aside
        self.assertEqual(doubts(listing=[("Film.2020.1080p.mkv", GB // 10), ("Film.2020.1080p.iso.part", 2 * GB)]),
                         ["its video is a small part of the download"])
        self.assertEqual(doubts(listing=[]), ["its file list could not be read"])
        row["year"] = None
        self.assertIn("its name does not give the film's exact year", doubts(listing=[("Film.1080p.mkv", 2 * GB)]))

    def test_a_check_download_needs_the_reviewers_concern_a_verified_copy_and_an_allowance(self):
        unverified = {"approve": False, "concern": acquisition_review.UNVERIFIED}
        attributes = [(ATTRIBUTES, "its quality is not in its name")]
        self.assertEqual(acquisition_review.check_allowed(unverified, attributes, 1, 0), (True, ""))
        self.assertTrue(acquisition_review.check_allowed(unverified, [(SWARM, "few"), (RANKING, "r1")], 1, 0)[0])
        refused = {
            "used": acquisition_review.check_allowed(unverified, attributes, 1, 1),
            "identity": acquisition_review.check_allowed(unverified, [(IDENTITY, "other titles share this name")], 1, 0),
            "files": acquisition_review.check_allowed(unverified, [(FILES, "the pack's files are not listed")], 1, 0),
            "concern": acquisition_review.check_allowed({"approve": False, "concern": "fails_request"}, attributes, 1, 0),
            "off": acquisition_review.check_allowed(unverified, attributes, 0, 0),
        }
        self.assertFalse(any(allowed for allowed, _ in refused.values()))
        self.assertIn("used its 1 check download", refused["used"][1])
        self.assertIn("code could not verify the copy itself: other titles share this name", refused["identity"][1])
        self.assertEqual(acquisition_review.REVIEW_SCHEMA["properties"]["concern"]["enum"], list(acquisition_review.CONCERNS))
        self.assertIn("concern", acquisition_review.REVIEW_SCHEMA["required"])
        self.assertIn("This is a check download", landing_notes({"check": {"concern": acquisition_review.UNVERIFIED}}))


class ProfileThresholdTests(unittest.TestCase):
    def test_the_review_thresholds_are_versioned_profile_fields(self):
        with environment():
            review, scouting, librarian = (profiles.resolve(r) for r in ("fetch_review", "fetch_scout", "librarian"))
            table = profiles.table(profiles.all_profiles())
        self.assertEqual((review.skip_review, review.skip_seeds, review.check_downloads), (1, 10, 1))
        self.assertEqual((scouting.skip_review, scouting.check_downloads), (0, 0))
        # Left out of the version while zero: roles that never use them keep theirs.
        self.assertNotIn("skip_review", librarian.contents())
        self.assertIn("skip_review", review.contents())
        self.assertIn("skipped for verified picks with 10+ seeds", table)
        self.assertIn("1 check download per request", table)
        custom = {"fetch_review": {"skip_review": 0, "skip_seeds": 25, "check_downloads": 2}}
        with environment(SPARROW_PROFILES=json.dumps(custom)):
            changed = profiles.resolve("fetch_review")
        self.assertEqual((changed.skip_review, changed.skip_seeds, changed.check_downloads), (0, 25, 2))
        self.assertNotEqual(changed.version, review.version)


class ReviewSkipTests(GateFixture):
    WANTED = {"1": [2]}

    def stock(self):
        self.rows = [indexed(21, "Fixture Show S01 1080p BluRay", 60, 20, 26), indexed(22, "Fixture Show S01 720p WEB", 8, 9, 26)]
        self.listings = {"21": [("Fixture Show S01/Fixture Show - S01E02 - Two Roads.mkv", 900 * 10**6)],
                         "22": [("Fixture Show S01/Fixture Show - S01E02.mkv", 400 * 10**6)]}

    async def test_a_verified_pick_downloads_without_the_review_and_the_ledger_says_why(self):
        self.stock()
        await self.run_turn(("find_releases", {}), ("propose_release", {"release": "r1", "reason": "verified pack file"}))

        self.reviewer.assert_not_awaited()
        [download] = self.downloads()
        self.assertEqual(download.metadata["wanted_files"], ["Fixture Show S01/Fixture Show - S01E02 - Two Roads.mkv"])
        [skip] = [r for r in ledger.load_enforcements(self.service.store) if r["rule"] == "review_skipped"]
        gate = profiles.resolve("fetch_review")
        self.assertEqual((skip["profile"], skip["profile_version"], skip["tool"]), ("fetch_review", gate.version, "propose_release"))
        self.assertIn("the title with the episode by its title", skip["detail"]["verified"])
        self.assertEqual(skip["detail"]["min_seeds"], 10)
        summary = ledger.summarize(self.service.store)
        reviews = summary["stats"]["reviews"]
        self.assertEqual((reviews["reviews"], reviews["skipped"], reviews["cost"]), (0, 1, 0))
        self.assertEqual(summary["titles"][0]["decisions"][0]["verdict"], "skipped")
        report = ledger.report(summary)
        self.assertIn("skipped (code verified) 1", report)
        self.assertIn("review_skipped 1", report)
        self.assertNotIn("pick_review", summary["titles"][0]["phases"])

    async def test_the_profile_can_review_every_pick(self):
        self.stock()
        with patch.dict("os.environ", {"SPARROW_PROFILES": json.dumps({"fetch_review": {"skip_review": 0}})}):
            self.verdict()
            await self.run_turn(("find_releases", {}), ("propose_release", {"release": "r1", "reason": "verified pack file"}))
        self.assertEqual(self.reviewer.await_count, 1)
        self.assertEqual(len(self.downloads()), 1)

    async def test_an_uncertain_pick_keeps_the_review(self):
        self.stock()
        self.namesakes = [{"id": 99, "name": self.TITLE, "first_air_date": "2009-01-01", "original_language": "en"}]
        self.verdict()
        ctx = ToolCtx(self.session, self.runtime)
        await self.tool("find_releases")(ctx, {})
        ctx.facts = {}
        await self.tool("propose_release")(ctx, {"release": "r2", "reason": "the second row"})
        self.assertEqual(self.reviewer.await_count, 1)
        self.assertEqual(ctx.facts["review_needed"], ["other titles share this name", "it has 8 seeds, under 10",
                                                      "code ranked r1 first"])


class EpisodeOrderReviewTests(GateFixture):
    async def test_a_file_found_under_another_number_is_still_reviewed(self):
        self.stock_disputed_season()
        self.verdict()
        ctx = ToolCtx(self.session, self.runtime)
        await self.tool("find_releases")(ctx, {})
        ctx.facts = {}
        await self.tool("propose_release")(ctx, {"release": "r1", "reason": "the pack's file by title"})
        self.assertEqual(self.reviewer.await_count, 1)
        self.assertEqual(ctx.facts["review_needed"], ["its file was found by the episode's title under another number"])


class CheckDownloadTests(GateFixture):
    MEDIA = "movie"
    TITLE = "Fixture Film"

    def stock(self):
        # Exact title and year, one named video each, but no quality in the name.
        self.rows = [indexed(31, "Fixture.Film.2020.WEB", 30, 1.8), indexed(32, "Fixture.Film.2020.Remastered", 20, 1.5)]
        self.listings = {"31": [("Fixture.Film.2020.WEB.mkv", 1800 * 10**6)],
                         "32": [("Fixture.Film.2020.Remastered.mkv", 1500 * 10**6)]}

    async def test_a_veto_only_for_unverified_quality_downloads_once_to_be_measured(self):
        self.stock()
        self.verdict(approve=False, reason="Its quality and audio are not shown in the name.",
                     concern=acquisition_review.UNVERIFIED, queries=["Fixture Film 2020 1080p"])
        await self.run_turn(("find_releases", {}), ("propose_release", {"release": "r1", "reason": "exact title and year"}))

        self.assertEqual(self.reviewer.await_count, 1)
        [download] = self.downloads()
        self.assertEqual(download.metadata["check"]["concern"], acquisition_review.UNVERIFIED)
        self.assertIn("This is a check download", landing_notes(download.metadata))
        self.assertEqual(self.service.store.get_session(self.session.id).model, "gpt-6-luna")  # no escalation
        [check] = [r for r in ledger.load_enforcements(self.service.store) if r["rule"] == "check_download"]
        self.assertEqual((check["profile"], check["detail"]["used"], check["detail"]["allowance"]), ("fetch_review", 1, 1))
        self.assertIn("its quality is not in its name", check["detail"]["doubts"])
        summary = ledger.summarize(self.service.store)
        self.assertEqual(summary["titles"][0]["decisions"][0]["verdict"], "checked")
        self.assertEqual(summary["stats"]["reviews"]["checked"], 1)
        self.assertIn("check downloads 1: verified 0, failed 0", ledger.report(summary))

        # The allowance is per request: the next such veto stands.
        self.verdict(approve=False, reason="Same doubt.", concern=acquisition_review.UNVERIFIED)
        ctx = ToolCtx(self.session, self.runtime)
        await self.tool("find_releases")(ctx, {})
        ctx.facts = {}
        reply = await self.tool("propose_release")(ctx, {"release": "r2", "reason": "the other copy"})
        self.assertTrue(str(reply).startswith("Vetoed."))
        self.assertIn("used its 1 check download", ctx.facts["check_refused"])
        self.assertEqual(ctx.facts["enforced"][0]["rule"], "check_download_refused")
        self.assertEqual(len(self.downloads()), 1)

    async def test_a_copy_code_cannot_verify_is_never_downloaded_to_check(self):
        self.stock()
        self.namesakes = [{"id": 99, "title": self.TITLE, "release_date": "1971-01-01", "original_language": "en"}]
        self.verdict(approve=False, reason="Quality unknown.", concern=acquisition_review.UNVERIFIED)
        self.verdict(approve=False, reason="Wrong film.", concern="wrong_title_or_episode")
        ctx = ToolCtx(self.session, self.runtime)
        await self.tool("find_releases")(ctx, {})
        ctx.facts = {}
        reply = await self.tool("propose_release")(ctx, {"release": "r1", "reason": "exact title and year"})
        self.assertTrue(str(reply).startswith("Vetoed."))
        self.assertIn("code could not verify the copy itself: other titles share this name", ctx.facts["check_refused"])
        self.namesakes = []
        await self.tool("find_releases")(ctx, {})
        ctx.facts = {}
        reply = await self.tool("propose_release")(ctx, {"release": "r1", "reason": "exact title and year"})
        self.assertTrue(str(reply).startswith("Vetoed."))  # an identity veto is never a check download
        self.assertNotIn("check_refused", ctx.facts)
        self.assertEqual(self.downloads(), [])

    async def test_the_model_cannot_mark_its_own_add_as_a_check(self):
        from backend.agents.node_tools import acquisition_tools

        self.stock()
        client_add = next(t for t in acquisition_tools(self.tb) if t.name == "client_add").handler
        await client_add(ToolCtx(self.session, self.runtime), {"info_hash": f"{31:040x}", "name": "Fixture.Film.2020.WEB",
                                                               "check": {"concern": "anything"}})
        [download] = self.downloads()
        self.assertNotIn("check", download.metadata)


if __name__ == "__main__":
    unittest.main()
