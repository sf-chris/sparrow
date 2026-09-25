"""Large observations remain retrievable, private and bounded across crashes."""

import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.agents.evidence import INLINE_CHARS, PAGE_CHARS
from backend.agents.models import AgentKind, AgentSession, Event, SessionStatus
from backend.agents.runtime import AgentRuntime, AgentSpec, ToolCtx, ToolDef
from backend.agents.store import AgentStore
from test_discovery import Block, response


class AgentEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AgentStore(self.temp.name)
        self.session = AgentSession(agent=AgentKind.DISCOVERY, model="fixture")
        self.store.save_session(self.session)
        self.observation = 'ā📺\n"\\' * 12_000 + "FINAL FILE: episode 201"
        self.handler = AsyncMock(side_effect=lambda *_: self.observation)
        self.source = ToolDef(
            "inspect", "Fixture observation", {"type": "object"}, self.handler
        )

        async def finish(ctx, args):
            ctx.close = True
            return "Verified fixture."

        self.spec = AgentSpec(
            "discovery",
            lambda: "fixture",
            AsyncMock(return_value="Fixture"),
            lambda _: [
                self.source,
                ToolDef("finish", "Finish", {"type": "object"}, finish),
            ],
        )
        self.runtime = self.reopen()

    def reopen(self):
        self.store = AgentStore(self.temp.name)
        runtime = AgentRuntime(self.store, lambda: "unused")
        runtime.register(self.spec)
        runtime._client = lambda: self.fail("Evidence checks must not call a provider")
        return runtime

    async def run_tool(self, block, session=None):
        session = session or self.session
        return await self.runtime._run_tool(
            ToolCtx(session, self.runtime),
            {tool.name: tool for tool in self.runtime.tools_for(session)},
            block,
        )

    async def test_pages_reconstruct_exact_unicode_observation_after_restart(self):
        block = Block("inspect", {})
        result = await self.run_tool(block)
        first = json.loads(result["content"])
        self.assertFalse(first["complete"])
        self.assertTrue(first["artifact_complete"])
        self.assertEqual(first["total_chars"], len(self.observation))
        self.assertEqual(first["total_bytes"], len(self.observation.encode()))
        self.assertEqual(
            first["sha256"], hashlib.sha256(self.observation.encode()).hexdigest()
        )
        self.assertLessEqual(len(result["content"]), INLINE_CHARS)
        self.runtime = self.reopen()
        self.assertEqual(await self.run_tool(block), result)
        self.handler.assert_awaited_once()
        fragments, page = [first["text"]], first
        while page["next_offset"] is not None:
            result = await self.run_tool(
                Block(
                    "evidence_read",
                    {
                        "evidence_id": first["evidence_id"],
                        "offset": page["next_offset"],
                    },
                )
            )
            self.assertNotIn("is_error", result)
            self.assertLessEqual(len(result["content"]), INLINE_CHARS)
            page = json.loads(result["content"])
            self.assertEqual(page["sha256"], first["sha256"])
            fragments.append(page["text"])
        self.assertEqual("".join(fragments), self.observation)
        # Reading pages must not recursively create more evidence artifacts.
        self.assertEqual(len(self.store.evidence.list(self.session)["observations"]), 1)

    async def test_real_loop_can_request_tail_before_finishing(self):
        calls = []

        async def model(session, system, tools):
            calls.append([tool.name for tool in tools])
            if len(calls) == 1:
                return response("inspect", {})
            last = json.loads(session.messages[-1]["content"][0]["content"])
            if len(calls) == 2:
                return response(
                    "evidence_read",
                    {
                        "evidence_id": last["evidence_id"],
                        "offset": last["total_chars"] - 22,
                    },
                )
            self.assertIn("episode 201", last["text"])
            return response("finish", {})

        self.runtime._call_api = model
        await self.runtime.wake(self.session.id, Event(kind="inspect_fixture"))
        self.assertEqual(len(calls), 3)
        self.assertTrue(
            all(
                "evidence_read" in tools and "evidence_list" in tools for tools in calls
            )
        )
        self.assertEqual(
            self.store.get_session(self.session.id).status, SessionStatus.CLOSED
        )

    async def test_observer_crash_preserves_result_and_evidence_together(self):
        async def crash(session, name, phase, *args):
            if phase == "completed":
                raise asyncio.CancelledError()

        block = Block("inspect", {})
        self.runtime._on_tool_activity = crash
        self.runtime._call_api = AsyncMock(
            return_value=SimpleNamespace(content=[block])
        )
        with self.assertRaises(asyncio.CancelledError):
            await self.runtime.wake(self.session.id, Event(kind="inspect_fixture"))
        self.runtime = self.reopen()
        loaded = self.store.get_session(self.session.id)
        self.runtime._repair_interrupted(loaded)
        result = loaded.messages[-1]["content"][0]
        page = json.loads(result["content"])
        self.assertEqual(
            self.store.evidence.read(loaded, page["evidence_id"])["sha256"],
            page["sha256"],
        )
        self.handler.assert_awaited_once()

    async def test_context_save_failure_rolls_back_evidence_and_result(self):
        block = Block("inspect", {})
        with patch.object(
            self.store, "_save_session", side_effect=OSError("disk failure")
        ):
            with self.assertRaises(OSError):
                await self.run_tool(block)
        self.assertEqual(self.store.evidence.list(self.session)["observations"], [])
        self.assertIsNone(self.store.invocation(self.session.id, block.id)["result"])
        replay = await self.run_tool(block)
        self.assertTrue(replay["is_error"])
        self.assertIn("uncertain", replay["content"])
        self.handler.assert_awaited_once()

    async def test_other_session_cannot_read_or_list_evidence_even_for_same_person(
        self,
    ):
        first = json.loads((await self.run_tool(Block("inspect", {})))["content"])
        other = AgentSession(agent=AgentKind.DISCOVERY, model="fixture")
        self.store.save_session(other)
        result = await self.run_tool(
            Block("evidence_read", {"evidence_id": first["evidence_id"]}), other
        )
        self.assertTrue(result["is_error"])
        self.assertIn("not found", result["content"])
        self.assertEqual(self.store.evidence.list(other)["observations"], [])
        # A forged owner on the same session identity must also be rejected.
        changed = AgentSession.from_dict(self.session.to_dict())
        changed.user_id = "another-person"
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.evidence.read(changed, first["evidence_id"])

    async def test_cancelled_session_cannot_retrieve_with_fresh_invocation(self):
        first = json.loads((await self.run_tool(Block("inspect", {})))["content"])
        self.session.status = SessionStatus.CLOSED
        self.store.save_session(self.session)
        result = await self.run_tool(
            Block("evidence_read", {"evidence_id": first["evidence_id"]})
        )
        self.assertTrue(result["is_error"])
        self.assertIn("authority changed", result["content"])

    async def test_pagination_rejects_invalid_ranges(self):
        first = json.loads((await self.run_tool(Block("inspect", {})))["content"])
        for args in (
            {"offset": -1},
            {"offset": True},
            {"offset": "0"},
            {"offset": first["total_chars"] + 1},
            {"limit": 0},
            {"limit": PAGE_CHARS + 1},
        ):
            with self.subTest(args=args):
                result = await self.run_tool(
                    Block(
                        "evidence_read", {"evidence_id": first["evidence_id"], **args}
                    )
                )
                self.assertTrue(result["is_error"])

    async def test_quotas_preserve_existing_evidence_and_report_uncertain_effects(self):
        first = json.loads((await self.run_tool(Block("inspect", {})))["content"])
        for constant in ("MAX_ARTIFACT_BYTES", "MAX_SESSION_BYTES", "MAX_TOTAL_BYTES"):
            with (
                self.subTest(quota=constant),
                patch("backend.agents.evidence." + constant, 1),
            ):
                block = Block("inspect", {})
                result = await self.run_tool(block)
                self.assertTrue(result["is_error"])
                error = json.loads(result["content"])
                self.assertFalse(error["complete"])
                self.assertIn("may already have happened", error["guidance"])
                self.assertEqual(
                    self.store.invocation(self.session.id, block.id)["result"], result
                )
        self.assertEqual(len(self.store.evidence.list(self.session)["observations"]), 1)
        self.assertEqual(
            self.store.evidence.read(self.session, first["evidence_id"])["sha256"],
            first["sha256"],
        )

    async def test_list_recovers_references_after_context_loss_and_paginates(self):
        ids = []
        for _ in range(22):
            ids.append(
                json.loads((await self.run_tool(Block("inspect", {})))["content"])[
                    "evidence_id"
                ]
            )
        self.session.messages = []
        self.store.save_session(self.session)
        self.runtime = self.reopen()
        first = json.loads((await self.run_tool(Block("evidence_list", {})))["content"])
        second = json.loads(
            (
                await self.run_tool(
                    Block("evidence_list", {"after": first["next_after"]})
                )
            )["content"]
        )
        self.assertIsNone(second["next_after"])
        self.assertEqual(
            [
                row["evidence_id"]
                for row in first["observations"] + second["observations"]
            ],
            ids,
        )

    async def test_migration_backs_up_old_sessions_before_adding_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = sqlite3.connect(root / "sparrow.db")
            old.execute("CREATE TABLE installed_fixture(value TEXT)")
            old.execute("INSERT INTO installed_fixture VALUES('preserve me')")
            old.commit()
            old.close()
            (root / ".migration-agent-recovery-1").write_text("agent-recovery-1")
            store = AgentStore(temp)
            backups = list(
                (root / "backups").glob("before-agent-evidence-1-*/sparrow.db")
            )
            self.assertEqual(len(backups), 1)
            with sqlite3.connect(backups[0]) as backup:
                self.assertEqual(
                    backup.execute("SELECT value FROM installed_fixture").fetchone()[0],
                    "preserve me",
                )
                self.assertIsNone(
                    backup.execute(
                        "SELECT name FROM sqlite_master WHERE name='agent_evidence'"
                    ).fetchone()
                )
            with store._connect() as db:
                self.assertEqual(
                    db.execute("SELECT value FROM installed_fixture").fetchone()[0],
                    "preserve me",
                )
            AgentStore(temp)
            self.assertEqual(
                len(list((root / "backups").glob("before-agent-evidence-1-*"))), 1
            )

    async def test_concurrent_sessions_cannot_overrun_server_evidence_quota(self):
        second = AgentSession(agent=AgentKind.DISCOVERY)
        self.store.save_session(second)
        for session in (self.session, second):
            self.store.start_invocation(session, "inspect-1", "inspect", {})
        text = "x" * (INLINE_CHARS + 1)
        result = {"type": "tool_result", "tool_use_id": "inspect-1", "content": text}
        with patch("backend.agents.evidence.MAX_TOTAL_BYTES", 2 * len(text) - 1):
            results = await asyncio.gather(
                *[
                    asyncio.to_thread(
                        self.store.finish_invocation, session, "inspect-1", result, {}
                    )
                    for session in (self.session, second)
                ]
            )
        self.assertEqual(sum(bool(row.get("is_error")) for row in results), 1)
        with self.store._connect() as db:
            self.assertEqual(
                db.execute("SELECT SUM(size_bytes) FROM agent_evidence").fetchone()[0],
                len(text),
            )

    async def test_escaped_previews_stay_bounded_and_keep_original_error(self):
        self.observation = "\x00" * (INLINE_CHARS + 1)
        result = await self.run_tool(Block("inspect", {}))
        self.assertLessEqual(len(result["content"]), INLINE_CHARS)
        page = json.loads(result["content"])
        self.assertEqual(page["text"], "\x00" * PAGE_CHARS)
        from backend.agents.runtime import ToolError

        self.handler.side_effect = ToolError(self.observation)
        error = await self.run_tool(Block("inspect", {}))
        self.assertTrue(error["is_error"])
        self.assertTrue(json.loads(error["content"])["artifact_complete"])
        self.assertTrue(json.loads(error["content"])["source_is_error"])

    async def test_late_observation_keeps_original_revision_and_cancelled_state(self):
        async def late_result(ctx, args):
            current = self.store.get_session(ctx.session.id)
            current.job_revision = 2
            current.status = SessionStatus.CLOSED
            self.store.save_session(current)
            return self.observation

        self.handler.side_effect = late_result
        result = await self.run_tool(Block("inspect", {}))
        self.assertEqual(json.loads(result["content"])["request_revision"], 1)
        self.assertEqual(
            self.store.get_session(self.session.id).status, SessionStatus.CLOSED
        )
