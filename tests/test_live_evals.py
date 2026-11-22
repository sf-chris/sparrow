"""The live role evaluation harness (tests/evals/run_live.py), exercised in its
free dry-run mode: scripted provider answers, every other host refused, and
real agents, runtime, tools and ledger in between."""

import json
import logging
import os
import socket
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.agents.node_executor import executable
from evals import live_harness, run_live
from evals.live_roles import LibrarianWorld, ROLES

FAST = ["discovery-01", "librarian-06", "fetch-01", "media-01"]


def args(**values):
    return Namespace(**{"dry_run": True, "max_dollars": 15.0, "per_run_dollars": 1.0, "runs": 1, "concurrency": 2,
                        "keep_state": False, "timeout_scale": 1.0, **values})


def real_network(*_args, **_kwargs):
    raise AssertionError("A dry run reached the real network.")


async def dry_run(case_ids, scripted=None, **values):
    cases = [c for c in run_live.load_cases() if c["id"] in case_ids]
    environment = {**run_live.HERMETIC, **run_live.DRY_KEYS, **run_live.DRY_MODELS}
    with patch.dict(os.environ, environment), patch.object(live_harness, "_ORIGINAL_ASYNC", real_network):
        return await run_live.main_async(args(**values), cases, scripted=scripted)


class CaseTests(unittest.TestCase):
    def test_the_30_contract_cases_and_a_separate_holdout_load(self):
        main = run_live.load_cases(("main",))
        holdout = run_live.load_cases(("holdout",))
        contracts = json.loads((Path(run_live.HERE) / "contracts-v1.json").read_text())["cases"]
        self.assertEqual(len(main), 30)
        # The same ids, roles and contract tests as the controlled suite.
        self.assertEqual({(c["id"], c["role"], c["contract"]) for c in main},
                         {(c["id"], c["role"], c["test"]) for c in contracts})
        self.assertGreaterEqual(len(holdout), 10)
        self.assertFalse({c["id"] for c in main} & {c["id"] for c in holdout})
        self.assertTrue(all(c["id"].startswith("holdout-") and c["set"] == "holdout" for c in holdout))
        for group in (main, holdout):
            self.assertEqual({c["role"] for c in group}, set(ROLES))
            self.assertEqual({c["kind"] for c in group}, {"ordinary", "adversarial"})
        self.assertEqual(len(run_live.load_cases()), len(main) + len(holdout))

    def test_paid_runs_need_explicit_consent(self):
        with patch.dict(os.environ, {"SPARROW_RUN_LIVE_EVALS": ""}), self.assertRaises(SystemExit) as stop:
            run_live.main(["--cases", "discovery-01"])
        self.assertIn("SPARROW_RUN_LIVE_EVALS=1", str(stop.exception))


class DryRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_records_outcomes_cost_and_configuration_without_the_network(self):
        result = await dry_run(FAST)
        runs = {r["case"]: r for r in result["runs"]}
        self.assertEqual(set(runs), set(FAST))
        for record in runs.values():
            self.assertEqual(record["status"], "passed", record)
            self.assertEqual(record["network_violations"], [])
            self.assertGreater(record["calls"]["provider"], 0)
            self.assertGreater(record["cost"]["ledger"], 0)
            self.assertAlmostEqual(record["cost"]["ledger"], record["cost"]["provider_meter"], places=4)
            self.assertEqual(record["models"]["requested"], record["models"]["actual"])
            self.assertTrue(record["profiles"] and all(p["version"] for p in record["profiles"]))
            self.assertTrue(record["actions"])
            self.assertGreater(record["latency_seconds"], 0)
        self.assertEqual(runs["fetch-01"]["models"]["requested"], ["cheap-model", "smart-model"])  # pick and review
        self.assertIn("pick_review", runs["fetch-01"]["calls"]["ledger_by_phase"])
        self.assertEqual(result["unattributed_network_violations"], 0)
        summary = result["summary"]
        self.assertEqual(set(summary["by_role"]), set(ROLES))
        self.assertEqual(summary["main"]["passed"], 4)
        self.assertEqual(summary["by_role"]["librarian"]["main"]["cases"]["librarian-06"]["pass_rate"], 1.0)
        self.assertIn("fetch_review", {p["id"] for p in result["configuration"]["profiles"]})
        self.assertAlmostEqual(result["spent"]["ledger"], sum(r["cost"]["ledger"] for r in result["runs"]), places=6)
        text = json.dumps(result)
        for secret in run_live.DRY_KEYS.values():
            self.assertNotIn(secret, text)
        self.assertNotIn("FIXGRP", text)  # no release or file names
        markdown = run_live.markdown(result)
        for heading in ("## Discovery", "## Fetch", "## Media", "## Librarian", "## Subtitle"):
            self.assertIn(heading, markdown)
        self.assertIn("| librarian-06 | main | ordinary | 1/1 |", markdown)

    async def test_the_dollar_cap_stops_new_runs_and_says_which_did_not_happen(self):
        cases = ["librarian-06", "librarian-01", "librarian-10", "discovery-01"]
        # A refused paid call ends that agent's turn with a logged crash: expected here.
        with patch.object(logging.getLogger("sparrow.agents"), "disabled", True):
            result = await dry_run(cases, max_dollars=0.0005, concurrency=1, runs=2)
        statuses = [r["status"] for r in result["runs"]]
        self.assertEqual(len(statuses), 8)  # every planned run is accounted for
        self.assertIn("not_run", statuses)
        self.assertLess(sum(1 for s in statuses if s in ("passed", "failed")), 3)
        self.assertTrue(result["stopped_at_cap"])
        self.assertTrue(all("cap" in r["error"] for r in result["runs"] if r["status"] in ("not_run", "cancelled_at_cap")))
        # No paid request goes out past the cap; at most the one in flight overshoots it.
        self.assertLess(result["spent"]["provider_meter"], 0.0005 + 0.002)
        not_run = result["summary"]["not_run"]
        self.assertTrue(not_run)
        self.assertIn("Runs that did not happen", run_live.markdown(result))

    async def test_a_forbidden_attempt_fails_the_run_even_when_the_tool_blocks_it(self):
        def reckless(world, transcript):
            if not transcript.called("evidence"):
                return live_harness.call("evidence")
            if not transcript.called("acquire"):
                return live_harness.call("acquire", wanted_episodes={"2": [1, 2, 3]})  # owned season 1 only
            return live_harness.call("finish", message="Requested the next season.")

        with patch.object(LibrarianWorld, "policy", reckless):
            result = await dry_run(["librarian-02"])
        record = result["runs"][0]
        self.assertEqual(record["status"], "failed")
        self.assertIn("no_out_of_scope_attempts", record["failed"])
        self.assertNotIn("requests_exactly_the_eligible_work", record["failed"])  # the tool refused it
        self.assertTrue(record["refused_tool_calls"])
        self.assertIn("librarian-02 run 1 [failed]: no_out_of_scope_attempts", run_live.markdown(result))

    async def test_any_request_beyond_the_provider_is_refused_and_fails_the_run(self):
        async def wander(world, number, body):
            async with httpx.AsyncClient() as client:
                with self.assertRaises(httpx.ConnectError):
                    await client.get("https://example.com/")
            with self.assertRaises(ConnectionRefusedError):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.connect(("93.184.216.34", 443))
                finally:
                    sock.close()

        with patch.object(LibrarianWorld, "before_provider", wander):
            result = await dry_run(["librarian-06"])
        record = result["runs"][0]
        self.assertIn("network_isolated", record["failed"])
        self.assertEqual(len(record["network_violations"]), 2 * record["calls"]["provider"])

    @unittest.skipUnless(executable("ffmpeg") and executable("ffprobe"), "Packaged media tools required")
    async def test_a_subtitle_review_runs_through_checkers_and_the_manager(self):
        result = await dry_run(["subtitle-01"])
        record = result["runs"][0]
        self.assertEqual(record["status"], "passed", record)
        self.assertEqual(set(record["calls"]["ledger_by_phase"]), {"subtitle_check", "subtitle_manager"})
        self.assertEqual(record["network_violations"], [])


if __name__ == "__main__":
    unittest.main()
