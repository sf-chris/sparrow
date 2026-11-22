"""Episode order settled in code (issue #3 P5; the 2026-11-22 audit's third
recommendation): when the packs number a season differently from TMDB, the
torrent's real file list is read by TMDB's episode titles, the file carrying
the wanted title is chosen and other copies of the disputed number are marked."""
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from backend.agents import scout
from backend.agents.release_match import numbered

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


if __name__ == "__main__":
    unittest.main()
