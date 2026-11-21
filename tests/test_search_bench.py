"""The search benchmark replays cassettes through the real scout, and its live
harness stops each request once a download is chosen, without adding one."""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.agents import ledger
from evals import search_bench as bench


def no_network(*args, **kwargs):
    raise AssertionError("The search benchmark must not reach the network here.")


class OfflineReplayTests(unittest.TestCase):
    def test_every_title_in_the_fixed_sets_has_a_cassette_and_replays_the_same_way_twice(self):
        sets = bench.title_sets()
        self.assertTrue({"anime", "films", "tv"} <= set(sets))
        cassettes = bench.load_cassettes(sets=sets)
        titles = bench.chosen_titles(sets)
        self.assertEqual({t["key"] for t in titles} - set(cassettes), set())
        with patch("httpx.AsyncClient", no_network):
            first = asyncio.run(bench.offline(titles, cassettes))
            second = asyncio.run(bench.offline(titles, cassettes))
        self.assertEqual(first["results"], second["results"])
        self.assertEqual(first["model_calls"], 0)
        summary = first["summary"]
        self.assertEqual(summary["titles"], len(titles))
        # A floor, not a target: the scout must keep finding these synthetic copies.
        self.assertGreaterEqual(summary["right_first"], 0.85 * summary["scored"])
        violet = next(r for r in first["results"] if r["key"] == "violet-evergarden")
        self.assertEqual((violet["kept"], violet["right_first"], violet["would_escalate"]), (0, True, True))
        report = bench.offline_report(first, before=second)
        self.assertIn("Before → after", report)
        self.assertIn("| anime | Naruto | S01E02 |", report)

    def test_the_committed_baseline_reads_and_compares(self):
        baseline = json.loads((bench.RESULTS / "search-offline.json").read_text())
        self.assertEqual(baseline["mode"], "offline")
        self.assertIn("right_first", baseline["summary"])
        self.assertTrue(baseline["results"])

    def test_the_index_is_simulated_by_its_own_rules(self):
        rows = [bench._row("k", r) for r in (
            ["1", "Naruto Shippuden - 002 [1080p]", 40, 0.4, 1],
            ["2", "[Group] Naruto - 002 [1080p]", 9, 0.4, 1],
            ["3", "Naruto.S01E02.720p", 0, 0.4, 1],
        )]
        self.assertEqual([r["id"] for r in bench.simulate(rows, "Naruto")], ["1", "2", "3"])  # most seeded first
        self.assertEqual([r["id"] for r in bench.simulate(rows, "Naruto -shippuden")], ["2", "3"])
        self.assertEqual([r["id"] for r in bench.simulate(rows, "naruto s01e02")], ["3"])
        self.assertEqual(bench.simulate(rows, "One Piece")[0]["name"], "No results returned")
        replay = bench.Replay([bench.Cassette({"key": "k", "rows": [], "index": {"Naruto S01E02": [["9", "Naruto S01E02 1080p", 5, 1, 1]]}})])
        replay.corpus = rows
        recorded = asyncio.run(replay.query("naruto  s01e02"))
        self.assertEqual([r["id"] for r in recorded], ["9"])  # a recording wins over the corpus
        self.assertEqual([r["id"] for r in asyncio.run(replay.query("Naruto"))], ["1", "2"])  # dead swarms filtered like apibay
        self.assertEqual(replay.simulated, ["Naruto"])

    def test_a_title_set_is_pinned_from_an_earlier_run(self):
        from backend.agents.models import Job
        from backend.agents.store import AgentStore

        with tempfile.TemporaryDirectory() as temp:
            store = AgentStore(temp)
            store.save_job(Job(title="Steins;Gate", tmdb_id=42509, year=2011, wanted_episodes={"1": [3]}))
            store.save_job(Job(title="Spirited Away", tmdb_id=129, year=2001, media_type="movie"))
            found = bench.titles_from_state(temp)
            titles = Path(temp) / "titles.json"
            titles.write_text(json.dumps({"version": 1, "sets": {}}))
            with patch("sys.stdout"):
                bench.main(["titles", "--from-state", temp, "--name", "batch-x", "--write", "--titles", str(titles)])
            saved = json.loads(titles.read_text())["sets"]["batch-x"]
        self.assertEqual([t["key"] for t in found], ["steins-gate", "spirited-away"])
        self.assertEqual(found[0]["episodes"], {"1": [3]})
        self.assertNotIn("episodes", found[1])
        self.assertEqual(saved, found)

    def test_the_live_mode_refuses_without_explicit_consent(self):
        with patch.dict(os.environ, {"SPARROW_RUN_LIVE_BENCH": ""}), self.assertRaises(SystemExit) as stop:
            bench.main(["live", "--set", "films"])
        self.assertIn("SPARROW_RUN_LIVE_BENCH=1", str(stop.exception))


class LiveHarnessTests(unittest.IsolatedAsyncioTestCase):
    """The live harness end to end with the network and the models replaced:
    cassettes answer the indexer and TMDB, a scripted model plays the Fetch
    Agent and the reviewer approves."""

    async def test_each_request_stops_once_its_download_is_chosen_and_nothing_downloads(self):
        sets = bench.title_sets()
        keys = ("chainsaw-man", "violet-evergarden", "spirited-away")
        titles = [t for t in bench.chosen_titles(sets) if t["key"] in keys]
        cassettes = bench.load_cassettes(sets=sets)
        replay = bench.Replay([cassettes[k] for k in keys])
        with tempfile.TemporaryDirectory() as temp:
            args = Namespace(batch="dry-1", state=str(Path(temp) / "state"), concurrency=2, minutes=5, max_dollars=1.0,
                             record=str(Path(temp) / "recorded"))
            added = []

            async def downloader(*_args, **_kwargs):
                added.append(_args)
                raise AssertionError("no transfer may start")

            with patch.object(bench.LiveBench, "POLL", 0.05), patch.object(bench.LiveBench, "TIMER", 0.05), \
                    patch("backend.agents.node_executor.Executor._download", downloader), \
                    patch.dict(os.environ, {"SPARROW_SMART_MODEL": "smart-model", "SPARROW_CHEAP_MODEL": "cheap-model"}), \
                    patch("httpx.AsyncClient", no_network):
                result = await bench.live(args, titles, replay=replay, model=bench.scripted_model())
            outcomes = {t["key"]: t["outcome"] for t in result["titles"]}
            self.assertEqual(outcomes["chainsaw-man"], "chosen")
            self.assertEqual(outcomes["spirited-away"], "chosen")
            self.assertEqual(outcomes["violet-evergarden"], "waiting")
            self.assertEqual(added, [])
            stats = result["stats"]
            self.assertEqual(stats["titles"], 3)
            self.assertEqual(stats["reviews"]["approved"], 2)
            self.assertGreater(stats["search_total"], 0)
            chainsaw = next(t for t in result["titles"] if t["key"] == "chainsaw-man")
            self.assertEqual(set(chainsaw["phases"]), {"scout_pick", "pick_review"})
            self.assertFalse(chainsaw["escalated"])
            self.assertIsNotNone(chainsaw["seconds_to_choice"])
            self.assertTrue(chainsaw["private"]["chosen"])
            # The ledger knows the run as one batch.
            store = bench.LiveBench(args.state, [], batch="x").state
            from backend.agents.store import AgentStore

            self.assertEqual(len(ledger.batches(AgentStore(str(store)))["dry-1"]), 3)
            # What is safe to commit holds no release names.
            public = json.dumps(bench.public(result))
            self.assertNotIn("SubsPlease", public)
            self.assertNotIn("private", public)
            self.assertNotIn("Waiting for the download", public)  # the agent's own words
            self.assertIn("Live search-only run dry-1", bench.live_report(result))
            # The run's answers are recorded as cassettes the offline replay reads.
            recorded = bench.load_cassettes(args.record, sets=sets)
            self.assertEqual(set(recorded), set(keys))
            self.assertTrue(recorded["chainsaw-man"].index)
            self.assertEqual(recorded["chainsaw-man"].entry["correct"], [])
            again = await bench.replay_title(titles[0], recorded[titles[0]["key"]])
            self.assertEqual(again["simulated_queries"], 0)  # every search the scout made was recorded


if __name__ == "__main__":
    unittest.main()
