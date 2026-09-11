"""Crash-boundary tests for durable event delivery and consequential tool results."""

import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.agents.models import (
    AgentSession,
    AgentKind,
    CaseState,
    Event,
    SessionStatus,
)
from backend.agents.runtime import AgentRuntime, AgentSpec, ToolCtx, ToolDef
from backend.agents.store import AgentStore
from test_discovery import Block, response


class AgentRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AgentStore(self.temp.name)
        self.session = AgentSession(agent=AgentKind.DISCOVERY, model="fixture")
        self.store.save_session(self.session)
        self.effects = []

        async def effect(ctx, args):
            self.effects.append(args)
            return {"receipt": len(self.effects)}

        async def finish(ctx, args):
            ctx.close = True
            ctx.close_reason = "Verified fixture outcome."
            return {"complete": True}

        self.tools = [
            ToolDef("effect", "Controlled effect", {"type": "object"}, effect),
            ToolDef("finish", "Verified completion", {"type": "object"}, finish),
        ]
        self.runtime = self.reopen()

    def reopen(self):
        self.store = AgentStore(self.temp.name)
        runtime = AgentRuntime(self.store, lambda: "fixture-key")
        runtime.register(
            AgentSpec(
                "discovery",
                lambda: "fixture",
                AsyncMock(return_value="fixture"),
                lambda _: self.tools,
            )
        )
        return runtime

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_queued_event_survives_restart_and_duplicate_delivery_costs_zero(
        self,
    ):
        event = Event(kind="evidence_changed", payload={"identity": "new-fact"})
        self.store.enqueue_delivery(self.session.id, event)
        runtime = self.reopen()
        runtime._call_api = AsyncMock(return_value=response("finish", {}))
        await runtime.wake(self.session.id, event)
        saved = self.store.get_session(self.session.id)
        self.assertIn("new-fact", json.dumps(saved.messages))
        self.assertEqual(self.store.pending_events(saved.id), [])
        await runtime.wake(self.session.id, event)
        self.assertEqual(runtime._call_api.call_count, 1)

    async def test_event_acknowledgement_rolls_back_if_context_cannot_be_saved(self):
        event = Event(kind="important")
        self.store.enqueue_delivery(self.session.id, event)
        with patch.object(
            self.store, "_save_session", side_effect=RuntimeError("disk failure")
        ):
            with self.assertRaises(RuntimeError):
                self.store.acknowledge_events(self.session, [event])
        self.assertEqual(
            [e.id for e in self.store.pending_events(self.session.id)], [event.id]
        )

    async def test_completed_tool_survives_crash_in_later_tool_without_repeat(self):
        async def interrupted(ctx, args):
            raise asyncio.CancelledError()

        self.tools.append(
            ToolDef("interrupt", "Crash", {"type": "object"}, interrupted)
        )
        first, second = Block("effect", {"item": 1}), Block("interrupt", {})
        self.runtime._call_api = AsyncMock(
            return_value=SimpleNamespace(content=[first, second])
        )
        with self.assertRaises(asyncio.CancelledError):
            await self.runtime.wake(self.session.id, Event(kind="start"))
        self.assertEqual(len(self.effects), 1)
        self.assertIsNotNone(self.store.invocation(self.session.id, first.id)["result"])
        runtime = self.reopen()
        runtime._call_api = AsyncMock(return_value=response("finish", {}))
        await runtime.wake(self.session.id, Event(kind="restart"))
        saved = self.store.get_session(self.session.id)
        results = saved.messages[2]["content"]
        self.assertIn('"receipt": 1', results[0]["content"])
        self.assertTrue(results[1]["is_error"])
        self.assertIn("may already have happened", results[1]["content"])
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(saved.outcome, CaseState.COMPLETED)

    async def test_crash_after_effect_before_result_reports_uncertainty_without_replay(
        self,
    ):
        block = Block("effect", {"item": 2})
        self.runtime._call_api = AsyncMock(
            return_value=SimpleNamespace(content=[block])
        )
        with patch.object(
            self.store, "finish_invocation", side_effect=asyncio.CancelledError()
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.runtime.wake(self.session.id, Event(kind="start"))
        self.assertEqual(len(self.effects), 1)
        runtime = self.reopen()
        runtime._call_api = AsyncMock(return_value=response("finish", {}))
        await runtime.wake(self.session.id, Event(kind="restart"))
        saved = self.store.get_session(self.session.id)
        result = saved.messages[2]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertNotIn("before this tool ran", result["content"])
        self.assertEqual(len(self.effects), 1)

    async def test_result_is_durable_before_observers_are_notified(self):
        async def observer(session, name, phase, args, content, error):
            if phase == "completed":
                self.assertIsNotNone(
                    self.store.invocation(session.id, block.id)["result"]
                )
                raise asyncio.CancelledError()

        block = Block("effect", {})
        self.runtime._on_tool_activity = observer
        self.runtime._call_api = AsyncMock(
            return_value=SimpleNamespace(content=[block])
        )
        with self.assertRaises(asyncio.CancelledError):
            await self.runtime.wake(self.session.id, Event(kind="start"))
        self.assertEqual(len(self.effects), 1)

    async def test_completed_control_is_recovered_without_another_model_call(self):
        async def observer(session, name, phase, args, content, error):
            if phase == "completed":
                raise asyncio.CancelledError()

        self.runtime._on_tool_activity = observer
        self.runtime._call_api = AsyncMock(return_value=response("finish", {}))
        with self.assertRaises(asyncio.CancelledError):
            await self.runtime.wake(self.session.id, Event(kind="start"))
        runtime = self.reopen()
        runtime._call_api = AsyncMock()
        await runtime.wake(self.session.id, Event(kind="restart"))
        runtime._call_api.assert_not_called()
        self.assertEqual(
            self.store.get_session(self.session.id).status, SessionStatus.CLOSED
        )

    async def test_tool_receipts_refuse_reused_id_with_changed_arguments(self):
        block = Block("effect", {"item": 1})
        ctx = ToolCtx(self.session, self.runtime)
        tool_map = {t.name: t for t in self.tools}
        first = await self.runtime._run_tool(ctx, tool_map, block)
        again = await self.runtime._run_tool(ctx, tool_map, block)
        self.assertEqual(first, again)
        self.assertEqual(len(self.effects), 1)
        block.input = {"item": 2}
        invalid = await self.runtime._run_tool(ctx, tool_map, block)
        self.assertTrue(invalid["is_error"])
        self.assertEqual(len(self.effects), 1)

    async def test_cancel_while_tool_is_running_cannot_resurrect_session(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(ctx, args):
            entered.set()
            await release.wait()
            ctx.close = True
            return {"finished": True}

        self.tools.append(ToolDef("slow", "Fixture", {"type": "object"}, slow))
        self.runtime._call_api = AsyncMock(return_value=response("slow", {}))
        task = asyncio.create_task(
            self.runtime.wake(self.session.id, Event(kind="start"))
        )
        await entered.wait()
        saved = self.store.get_session(self.session.id)
        saved.status, saved.outcome = SessionStatus.CLOSED, CaseState.CANCELLED
        self.store.save_session(saved)
        release.set()
        await task
        self.assertEqual(self.store.get_session(saved.id).outcome, CaseState.CANCELLED)

    async def test_new_subscription_session_cannot_reset_spend_or_uncertain_allowance(
        self,
    ):
        self.session.agent = AgentKind.LIBRARIAN
        self.session.download_id = "subscription"
        self.session.budget_scope = "subscription:fixture"
        self.session.spend.dollars = 2.95
        self.store.save_session(self.session)
        replacement = AgentSession(
            agent=AgentKind.LIBRARIAN,
            download_id="subscription",
            model="fixture",
            budget_scope=self.session.budget_scope,
        )
        self.store.save_session(replacement)
        spec = AgentSpec(
            "librarian",
            lambda: "fixture",
            AsyncMock(return_value="fixture"),
            lambda _: self.tools,
        )
        self.runtime.register(spec)
        self.runtime._call_api = AsyncMock()
        _, limited = await self.runtime._budgeted_call(
            replacement, "fixture", self.tools, spec
        )
        self.assertTrue(limited)
        self.runtime._call_api.assert_not_called()
        self.session.spend.dollars = 0
        self.store.save_session(self.session)
        with self.store._connect() as db:
            db.execute(
                "INSERT INTO reasoning_reservations VALUES(?,?,?,?)",
                ("uncertain", self.session.id, 2.95, 1),
            )
        _, limited = await self.runtime._budgeted_call(
            replacement, "fixture", self.tools, spec
        )
        self.assertTrue(limited)
        self.runtime._call_api.assert_not_called()

    async def test_actions_after_finish_in_same_batch_are_refused(self):
        self.runtime._call_api = AsyncMock(
            return_value=SimpleNamespace(
                content=[Block("finish", {}), Block("effect", {})]
            )
        )
        await self.runtime.wake(self.session.id, Event(kind="start"))
        self.assertEqual(self.effects, [])
        self.assertTrue(
            self.store.get_session(self.session.id).messages[-1]["content"][1][
                "is_error"
            ]
        )

    async def test_boot_replays_unrouted_fact_and_recovers_interrupted_discovery(self):
        from backend.agents.service import AgentService
        from backend.storage import Storage

        storage = Storage(self.temp.name)
        await storage.load_all()
        service = AgentService(storage, self.temp.name, AsyncMock())
        service.runtime.register(self.runtime._specs["discovery"])
        service.runtime._call_api = AsyncMock(return_value=response("finish", {}))
        event = Event(kind="new_evidence", session_id=self.session.id)
        service.store.enqueue_event(event)
        with patch("backend.agents.service.asyncio.sleep", new=AsyncMock()):
            await service._boot_recovery()
        await asyncio.gather(*list(service._wake_tasks))
        self.assertEqual(service.store.unrouted_events(), [])
        self.assertEqual(
            service.store.get_session(self.session.id).outcome, CaseState.COMPLETED
        )
        self.assertEqual(service.runtime._call_api.call_count, 1)
        await service.shutdown()

    async def test_boot_preserves_librarian_retry_timer(self):
        from backend.agents.service import AgentService
        from backend.storage import Storage

        storage = Storage(self.temp.name)
        await storage.load_all()
        service = AgentService(storage, self.temp.name, AsyncMock())
        self.session.agent = AgentKind.LIBRARIAN
        self.session.wake_at = 9999999999
        self.session.wake_reason = "Waiting for provider recovery."
        service.store.save_session(self.session)
        with patch("backend.agents.service.asyncio.sleep", new=AsyncMock()):
            await service._boot_recovery()
        saved = service.store.get_session(self.session.id)
        self.assertEqual(saved.wake_at, self.session.wake_at)
        self.assertEqual(saved.wake_reason, self.session.wake_reason)
        await service.shutdown()

    async def test_cancel_from_completion_observer_is_not_overwritten(self):
        async def observer(session, name, phase, args, content, error):
            if phase == "completed":
                current = self.store.get_session(session.id)
                current.status = SessionStatus.CLOSED
                current.outcome = CaseState.CANCELLED
                self.store.save_session(current)

        self.runtime._on_tool_activity = observer
        self.runtime._call_api = AsyncMock(return_value=response("finish", {}))
        await self.runtime.wake(self.session.id, Event(kind="start"))
        self.assertEqual(
            self.store.get_session(self.session.id).outcome, CaseState.CANCELLED
        )
