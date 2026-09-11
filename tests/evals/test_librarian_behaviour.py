"""Opt-in real-model tests against personal subscription/tool contracts.

SPARROW_RUN_AGENT_EVALS=1 ANTHROPIC_API_KEY=... .venv/bin/python -m unittest discover -s tests/evals
SPARROW_EVAL_RUNS defaults to three independent runs of each case.
SPARROW_EVAL_REPORT overrides the default ignored JSONL evidence destination.
No download worker, live catalogue request or personal media is used.
"""

import os
import unittest
from backend.agents.models import SessionStatus
from tests.evals.librarian_fixture import LibrarianFixture, append_report

RUN_EVALS = os.getenv("SPARROW_RUN_AGENT_EVALS") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)
EVAL_RUNS = int(os.getenv("SPARROW_EVAL_RUNS", "3"))


@unittest.skipUnless(RUN_EVALS, "live agent evals require opt-in and an API key")
class LibrarianBehaviourEvals(unittest.IsolatedAsyncioTestCase):
    async def evaluate(self, scenario):
        self.assertGreaterEqual(
            EVAL_RUNS, 3, "Model selection requires three independent repetitions"
        )
        for run in range(EVAL_RUNS):
            with self.subTest(run=run):
                fixture = await LibrarianFixture().start(
                    scenario, os.environ["ANTHROPIC_API_KEY"]
                )
                try:
                    session, report = await fixture.run()
                    append_report({**report, "run": run + 1, "mode": "live_model"})
                    self.assertEqual(session.status, SessionStatus.CLOSED, report)
                    self.assertIn(
                        "evidence", [a["tool"] for a in report["attempts"]], report
                    )
                    self.assertEqual(report["forbidden_attempts"], [], report)
                    self.assertEqual(
                        report["jobs"],
                        [fixture.expected] if fixture.expected else [],
                        report,
                    )
                    self.assertFalse(
                        any(a.get("is_error") for a in report["attempts"]), report
                    )
                finally:
                    await fixture.close()

    async def test_incident_replay_owned_season_one_monitoring_off(self):
        await self.evaluate("season_one_only")

    async def test_keep_current_acquires_only_the_new_episode(self):
        await self.evaluate("new_episode")
