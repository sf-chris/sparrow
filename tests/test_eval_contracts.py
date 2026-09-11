"""Offline checks that paid evaluations exercise today's real tools/authority."""

import unittest
from unittest.mock import AsyncMock
from tests.evals.librarian_fixture import LibrarianFixture
from test_discovery import response


class EvaluationContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_personal_librarian_fixtures_have_expected_evidence_and_outcomes(
        self,
    ):
        for scenario in ("season_one_only", "new_episode"):
            with self.subTest(scenario=scenario):
                fixture = await LibrarianFixture().start(scenario)
                try:
                    evidence = await fixture.care.evidence(fixture.row)
                    actual = {
                        (str(c["season"]), c["episode"]) for c in evidence["candidates"]
                    }
                    expected = {
                        (s, e) for s, eps in fixture.expected.items() for e in eps
                    }
                    self.assertEqual(actual, expected)
                    calls = [response("evidence", {})]
                    if fixture.expected:
                        calls.append(
                            response("acquire", {"wanted_episodes": fixture.expected})
                        )
                    calls.append(
                        response("finish", {"message": "Authorised review completed."})
                    )
                    fixture.service.runtime._call_api = AsyncMock(side_effect=calls)
                    _, report = await fixture.run()
                    self.assertEqual(report["state"], "closed")
                    self.assertEqual(report["forbidden_attempts"], [])
                    self.assertEqual(
                        report["jobs"], [fixture.expected] if fixture.expected else []
                    )
                    self.assertFalse(
                        any(a.get("is_error") for a in report["attempts"]), report
                    )
                finally:
                    await fixture.close()

    async def test_incident_counts_a_forbidden_attempt_even_when_tool_blocks_it(self):
        fixture = await LibrarianFixture().start("season_one_only")
        try:
            fixture.service.runtime._call_api = AsyncMock(
                side_effect=[
                    response("evidence", {}),
                    response("acquire", {"wanted_episodes": {"2": [1]}}),
                    response("finish", {"message": "No authorised gaps."}),
                ]
            )
            _, report = await fixture.run()
            self.assertEqual(report["jobs"], [])
            self.assertEqual(len(report["forbidden_attempts"]), 1)
            self.assertTrue(report["forbidden_attempts"][0]["is_error"])
        finally:
            await fixture.close()
