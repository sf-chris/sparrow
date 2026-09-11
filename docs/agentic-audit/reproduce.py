"""Reproduce the 11 September agent audit findings without provider calls.

From the repository root:
    .venv/bin/python docs/agentic-audit/reproduce.py

Uses the current curation fixture, temporary state and a controlled model failure.
Outputs observations; future fixes should change the recorded behavior.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests"))

from test_curation import CurationTests
from test_discovery import response
from backend.agents.models import AgentKind, AgentSession
from backend.agents.runtime import ToolCtx, ToolError


async def main():
    fixture = CurationTests()
    await fixture.asyncSetUp()
    findings = {}
    try:
        row = fixture.follow()
        fixture.service.runtime._call_api = AsyncMock(return_value=None)
        await fixture.care.check(row["id"])
        first = fixture.care.row(row["id"])
        calls = fixture.service.runtime._call_api.call_count
        await fixture.care.check(row["id"])
        current = fixture.care.row(row["id"])
        session = fixture.service.store.get_session(current["data"]["session_id"])
        findings["failed_review_unchanged_recheck"] = {
            "calls_after_failure": calls,
            "calls_after_recheck": fixture.service.runtime._call_api.call_count,
            "session_state": session.status.value,
            "wake_at": session.wake_at,
            "fingerprint_retained": current["data"]["fingerprint"]
            == first["data"]["fingerprint"],
            "message": current["data"]["message"],
            "successful_fingerprint_matches_observation": bool(
                current["data"].get("observed_fingerprint")
            )
            and current["data"].get("fingerprint")
            == current["data"].get("observed_fingerprint"),
        }
        if session.wake_at:
            fixture.service.runtime._call_api = AsyncMock(
                return_value=response("finish", {"message": "Reviewed after recovery."})
            )
            with patch("time.time", return_value=session.wake_at + 1):
                await fixture.care.check(row["id"])
            await fixture.care.check(row["id"])
            recovered = fixture.care.row(row["id"])
            findings["retry_after_recovery"] = {
                "same_session": recovered["data"]["session_id"] == session.id,
                "state": fixture.service.store.get_session(session.id).status.value,
                "calls_including_completed_idle_recheck": fixture.service.runtime._call_api.call_count,
                "successful_fingerprint_matches_observation": recovered["data"][
                    "fingerprint"
                ]
                == recovered["data"]["observed_fingerprint"],
            }
        spec = fixture.service.runtime._specs[AgentKind.LIBRARIAN.value]
        legacy_session = AgentSession(agent=AgentKind.LIBRARIAN)
        definitions = spec.tools(legacy_session)
        evidence = next(tool for tool in definitions if tool.name == "evidence")
        result = {"tools": [tool.name for tool in definitions]}
        try:
            await evidence.handler(
                ToolCtx(session=legacy_session, runtime=fixture.service.runtime), {}
            )
            result["evidence_error"] = None
        except ToolError as exc:
            result["evidence_error"] = str(exc)
        findings["legacy_live_eval_session"] = result
    finally:
        await fixture.asyncTearDown()
    print(json.dumps(findings, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
