import unittest

from backend.agents import openai_loop
from backend.agents.runtime import calculate_usage_cost, rates_for_model


class OpenAILoopTests(unittest.TestCase):
    def test_history_round_trips_tool_calls_results_and_reasoning(self):
        answer = openai_loop.response_object(
            {
                "status": "completed",
                "output": [
                    {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque", "summary": []},
                    {"type": "message", "content": [{"type": "output_text", "text": "Checking."}]},
                    {"type": "function_call", "call_id": "call_1", "name": "page", "arguments": '{"page": 2}'},
                    {"type": "function_call", "call_id": "call_2", "name": "report", "arguments": "not json"},
                ],
                "usage": {"input_tokens": 1200, "output_tokens": 90, "input_tokens_details": {"cached_tokens": 1000}},
            }
        )
        self.assertEqual([b.type for b in answer.content], ["openai_reasoning", "text", "tool_use", "tool_use"])
        self.assertEqual(answer.content[2].input, {"page": 2})
        self.assertIn("_unparsed_arguments", answer.content[3].input)
        self.assertEqual((answer.usage.input_tokens, answer.usage.cache_read_input_tokens), (200, 1000))

        history = [
            {"role": "user", "content": "[wake] check the subtitles"},
            {"role": "assistant", "content": [b.to_dict() for b in answer.content]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": "Page 2 of 9"},
                    {"type": "tool_result", "tool_use_id": "call_2", "is_error": True, "content": [{"type": "text", "text": "Bad input."}]},
                ],
            },
        ]
        items = openai_loop.input_items(history)
        self.assertEqual(items[0], {"role": "user", "content": "[wake] check the subtitles"})
        self.assertEqual(items[1]["type"], "reasoning")
        self.assertEqual(items[2], {"role": "assistant", "content": "Checking."})
        self.assertEqual(items[3]["type"], "function_call")
        self.assertEqual(items[5], {"type": "function_call_output", "call_id": "call_1", "output": "Page 2 of 9"})
        self.assertEqual(items[6]["output"], "Error: Bad input.")

        body = openai_loop.request_body(
            "gpt-6-sol", "orders", [{"name": "page", "description": "d", "input_schema": {"type": "object"}}], history,
            max_tokens=100, effort="medium", cache_key="session",
        )
        self.assertEqual(body["tools"][0]["parameters"], {"type": "object"})
        self.assertFalse(body["store"])
        self.assertEqual(body["reasoning"], {"effort": "medium"})

    def test_openai_models_are_routed_and_priced(self):
        self.assertTrue(openai_loop.is_openai_model("gpt-6-sol"))
        self.assertFalse(openai_loop.is_openai_model("claude-opus-5-5"))
        cost, _ = calculate_usage_cost("gpt-6-sol", 1_000_000, 1_000_000, 0, 1_000_000)
        self.assertAlmostEqual(cost, 2.0 + 10.0 + 0.20)
        self.assertEqual(rates_for_model("gpt-6-luna")["cache_read"], 0.01)


class HistoryTrimTests(unittest.TestCase):
    def test_a_trimmed_history_never_keeps_a_result_without_its_call(self):
        from backend.agents.models import AgentSession
        from backend.agents.runtime import HISTORY_TRIM_AT, AgentRuntime

        session = AgentSession(messages=[{"role": "user", "content": "[wake] find episode 2"}])
        for n in range(HISTORY_TRIM_AT):
            session.messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"call_{n}", "name": "wake_me", "input": {}}]})
            # Wake text is merged into the message holding the tool result.
            session.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"call_{n}", "content": "Hibernating."},
                {"type": "text", "text": f"[wake] timer {n}"},
            ]})
        AgentRuntime._trim_history(None, session)
        self.assertLess(len(session.messages), HISTORY_TRIM_AT)
        calls = set()
        for message in session.messages:
            for block in message["content"] if isinstance(message["content"], list) else []:
                if block.get("type") == "tool_use":
                    calls.add(block["id"])
                if block.get("type") == "tool_result":
                    self.assertIn(block["tool_use_id"], calls)
        self.assertEqual(session.messages[2]["content"], [{"type": "text", "text": f"[wake] timer {HISTORY_TRIM_AT - 40}"}])
        openai_loop.input_items(session.messages)


class MissingKeyTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_missing_openai_key_is_reported_not_retried(self):
        import tempfile
        from unittest.mock import AsyncMock, patch
        from backend.agents.models import AgentKind, AgentSession
        from backend.agents.runtime import AgentRuntime, AgentSpec
        from backend.agents.store import AgentStore

        with tempfile.TemporaryDirectory() as folder:
            runtime = AgentRuntime(AgentStore(folder), lambda: "")
            runtime._openai_key_getter = lambda: ""
            runtime.register(AgentSpec(kind=AgentKind.FETCH.value, model=lambda: "gpt-6-sol", system=AsyncMock(return_value="orders"), tools=lambda s: []))
            session = AgentSession(agent=AgentKind.FETCH, model="gpt-6-sol")
            with patch.object(openai_loop, "create", AsyncMock()) as create:
                with self.assertRaisesRegex(openai_loop.OpenAIStatusError, "needs an OpenAI key"):
                    await runtime._call_openai(session, "orders", [])
                create.assert_not_called()

    async def test_a_refused_call_releases_its_budget_hold(self):
        import tempfile
        from unittest.mock import AsyncMock
        from backend.agents.models import AgentKind, AgentSession
        from backend.agents.runtime import AgentRuntime, AgentSpec
        from backend.agents.store import AgentStore

        with tempfile.TemporaryDirectory() as folder:
            runtime = AgentRuntime(AgentStore(folder), lambda: "")
            runtime._openai_key_getter = lambda: ""
            spec = AgentSpec(kind=AgentKind.FETCH.value, model=lambda: "gpt-6-sol", system=AsyncMock(return_value="orders"), tools=lambda s: [])
            runtime.register(spec)
            session = AgentSession(agent=AgentKind.FETCH, model="gpt-6-sol")
            runtime.store.save_session(session)
            for _ in range(3):
                with self.assertRaises(openai_loop.OpenAIStatusError):
                    await runtime._budgeted_call(session, "orders", [], spec)
            with runtime.store._connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM reasoning_reservations").fetchone()[0], 0)


class EscalationTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_session_escalated_to_claude_sends_no_openai_reasoning(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from backend.agents.models import AgentKind, AgentSession
        from backend.agents.runtime import AgentRuntime, AgentSpec
        from backend.agents.store import AgentStore

        history = [
            {"role": "user", "content": "[wake] find episode 2"},
            {"role": "assistant", "content": [
                {"type": "openai_reasoning", "item": {"type": "reasoning", "encrypted_content": "opaque"}},
                {"type": "tool_use", "id": "call_1", "name": "escalate_model", "input": {}},
            ]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "Escalated."}]},
            {"role": "assistant", "content": [{"type": "openai_reasoning", "item": {"type": "reasoning"}}]},
        ]
        with tempfile.TemporaryDirectory() as folder:
            runtime = AgentRuntime(AgentStore(folder), lambda: "key")
            runtime.register(AgentSpec(kind=AgentKind.FETCH.value, model=lambda: "claude-opus-5-5", system=AsyncMock(return_value="orders"), tools=lambda s: []))
            client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value="answer")), close=AsyncMock())
            runtime._client = lambda: client
            session = AgentSession(agent=AgentKind.FETCH, model="claude-opus-5-5", messages=history)
            self.assertEqual(await runtime._call_api(session, "orders", []), "answer")
        sent = client.messages.create.await_args.kwargs["messages"]
        self.assertNotIn("openai_reasoning", str(sent))
        self.assertEqual(sent[1]["content"], [history[1]["content"][1]])
        self.assertEqual(sent[3]["content"], "(continuing)")
        self.assertEqual(history[1]["content"][0]["type"], "openai_reasoning")  # kept for OpenAI calls
