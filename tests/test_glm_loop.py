"""Z.ai GLM provider: wire translation, effort, prices, routing and errors.
Every answer here is a recorded Chat Completions shape; nothing is sent."""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from backend.agents import glm_loop, altprovider_loop, profiles, providers, subtitle_contract
from backend.agents.runtime import calculate_usage_cost, rates_for_model

# A real glm-5.3-flash tool-call answer (2026-11-25), ids shortened.
TOOL_ANSWER = {
    "choices": [{"finish_reason": "tool_calls", "index": 0, "message": {
        "role": "assistant", "content": "", "reasoning_content": "Look it up first.",
        "tool_calls": [
            {"id": "call_1", "index": 0, "type": "function",
             "function": {"name": "tmdb_lookup", "arguments": "{\"title\":\"Four Lions\",\"year\":2010}"}},
            {"id": "call_2", "index": 1, "type": "function", "function": {"name": "note", "arguments": {"text": "x"}}},
        ]}}],
    "model": "glm-5.3-flash",
    "usage": {"completion_tokens": 21, "prompt_tokens": 1206, "prompt_tokens_details": {"cached_tokens": 1000},
              "total_tokens": 1227},
}


class TranslationTests(unittest.TestCase):
    def test_answer_round_trips_reasoning_tool_calls_and_cached_usage(self):
        answer = glm_loop.response_object(TOOL_ANSWER)
        self.assertEqual([b.type for b in answer.content], ["glm_reasoning", "tool_use", "tool_use"])
        self.assertEqual(answer.content[1].input, {"title": "Four Lions", "year": 2010})
        self.assertEqual(answer.content[2].input, {"text": "x"})  # arguments may arrive as an object
        self.assertEqual(answer.stop_reason, "tool_use")
        self.assertEqual((answer.usage.input_tokens, answer.usage.cache_read_input_tokens, answer.usage.output_tokens),
                         (206, 1000, 21))

        history = [
            {"role": "user", "content": "[wake] what is the runtime?"},
            {"role": "assistant", "content": [b.to_dict() for b in answer.content]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "97 minutes"},
                {"type": "tool_result", "tool_use_id": "call_2", "is_error": True,
                 "content": [{"type": "text", "text": "Bad input."}]},
                {"type": "text", "text": "[event] the download landed"},
            ]},
        ]
        messages = glm_loop.chat_messages("orders", history)
        self.assertEqual(messages[0], {"role": "system", "content": "orders"})
        self.assertEqual(messages[1], {"role": "user", "content": "[wake] what is the runtime?"})
        self.assertEqual(messages[2]["reasoning_content"], "Look it up first.")
        self.assertEqual(json.loads(messages[2]["tool_calls"][0]["function"]["arguments"]),
                         {"title": "Four Lions", "year": 2010})
        self.assertEqual(messages[3], {"role": "tool", "tool_call_id": "call_1", "content": "97 minutes"})
        self.assertEqual(messages[4]["content"], "Error: Bad input.")
        self.assertEqual(messages[5], {"role": "user", "content": "[event] the download landed"})

    def test_request_always_sends_an_effort_and_preserves_thinking(self):
        tools = [{"name": "page", "description": "d", "input_schema": {"type": "object"}}]
        body = glm_loop.request_body("glm-5.3", "orders", tools, [], max_tokens=100, effort="")
        self.assertEqual(body["reasoning_effort"], "low")  # never the provider's max default
        self.assertEqual(body["thinking"], {"type": "enabled", "clear_thinking": False})
        self.assertEqual(body["tools"][0]["function"]["parameters"], {"type": "object"})
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(glm_loop.request_body("glm-5.3", "o", [], [], max_tokens=1, effort="high")["reasoning_effort"],
                         "high")
        self.assertNotIn("tools", glm_loop.request_body("glm-5.3", "o", [], [], max_tokens=1))

    def test_other_providers_never_see_glm_reasoning(self):
        history = [{"role": "assistant", "content": [{"type": "glm_reasoning", "text": "hm"},
                                                     {"type": "tool_use", "id": "c", "name": "n", "input": {}}]},
                   {"role": "assistant", "content": [{"type": "glm_reasoning", "text": "hm"}]}]
        sent = altprovider_loop.provider_messages(history)
        self.assertNotIn("glm_reasoning", str(sent))
        self.assertEqual(sent[1]["content"], "(continuing)")
        self.assertNotIn("glm_reasoning", str(altprovider_loop.input_items(history)))

    def test_altprovider_reasoning_is_not_sent_to_glm(self):
        history = [{"role": "assistant", "content": [{"type": "altprovider_reasoning", "item": {"type": "reasoning"}},
                                                     {"type": "text", "text": "Done."}]}]
        self.assertEqual(glm_loop.chat_messages("", history), [{"role": "assistant", "content": "Done."}])


class RoutingPricingEffortTests(unittest.TestCase):
    def test_models_route_to_their_provider(self):
        self.assertEqual(providers.provider_for("glm-5.3-flash"), providers.GLM)
        self.assertEqual(providers.provider_for("smart-model"), providers.ALTPROVIDER)
        self.assertEqual(providers.provider_for("assistant-small"), providers.PROVIDER)
        self.assertFalse(altprovider_loop.is_altprovider_model("glm-5.3"))
        self.assertTrue(providers.reads_images("glm-5.3-flash"))
        self.assertFalse(providers.reads_images("glm-5.3"))  # text only

    def test_glm_list_prices(self):
        self.assertEqual(rates_for_model("glm-5.3-flash"), {"input": 0.15, "output": 0.5, "cache_write": 0.15,
                                                            "cache_read": 0.03})
        cost, _ = calculate_usage_cost("glm-5.3", 1_000_000, 1_000_000, 0, 1_000_000)
        self.assertAlmostEqual(cost, 1.40 + 4.40 + 0.26)
        self.assertEqual(rates_for_model("glm-9")["input"], 1.40)  # unlisted GLM: never priced as Assistant
        self.assertAlmostEqual(subtitle_contract.cost("glm-5.3-flash", {"input_tokens": 1_000_000,
                                                                        "output_tokens": 1_000_000}), 0.65)

    def test_effort_maps_to_the_levels_glm_accepts(self):
        for requested, sent in (("", "low"), ("medium", "low"), ("high", "high"), ("xhigh", "high"), ("max", "max"),
                                ("nonsense", "low")):
            self.assertEqual(profiles.effort_for("glm-5.3", requested), sent)

    def test_role_defaults_on_glm(self):
        env = {"SPARROW_CHEAP_MODEL": "glm-5.3-flash", "SPARROW_SMART_MODEL": "glm-5.3",
               "SPARROW_SUBTITLE_MODEL": "glm-5.3", "SPARROW_SUBTITLE_CONTRACTOR": "glm-5.3-flash"}
        with patch.dict(os.environ, env):
            for role in ("SPARROW_PROFILES", "SPARROW_SUBTITLE_EFFORT", "SPARROW_SUBTITLE_CONTRACTOR_EFFORT",
                         "SPARROW_FETCH_SCOUT"):
                os.environ.pop(role, None)
            sent = {p.id: (p.model, p.effort_sent) for p in profiles.all_profiles()}
        self.assertEqual(sent["fetch_scout"], ("glm-5.3-flash", "low"))
        self.assertEqual(sent["fetch_review"], ("glm-5.3", "low"))
        self.assertEqual(sent["fetch_escalation"], ("glm-5.3", "high"))
        self.assertEqual(sent["subtitle_manager"], ("glm-5.3", "high"))
        self.assertEqual(sent["subtitle_reader"], ("glm-5.3-flash", "low"))


class StructuredCallerTests(unittest.IsolatedAsyncioTestCase):
    SCHEMA = {"type": "object", "properties": {"verdict": {"enum": ["approve", "veto"]}, "reason": {"type": "string"}},
              "required": ["verdict", "reason"], "additionalProperties": False}

    @staticmethod
    def answer(text, prompt=100, completion=20):
        return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                          "prompt_tokens_details": {"cached_tokens": 0}}}

    async def test_a_valid_answer_is_returned_with_usage(self):
        post = AsyncMock(return_value=self.answer('{"verdict": "approve", "reason": "exact match"}'))
        with patch.object(glm_loop, "post", post):
            call = subtitle_contract.caller_for("glm-5.3", "", "", "low", glm_key="k")
            data, usage = await call("Review.", "Pick 1.", self.SCHEMA)
        self.assertEqual(data["verdict"], "approve")
        self.assertEqual(usage["input_tokens"], 100)
        body = post.await_args.args[1]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIn('"required":["verdict","reason"]', body["messages"][0]["content"])
        self.assertEqual(body["reasoning_effort"], "low")

    async def test_a_bad_answer_is_retried_once_naming_the_problem(self):
        post = AsyncMock(side_effect=[self.answer('{"verdict": "maybe"}'),
                                      self.answer('{"verdict": "veto", "reason": "wrong film"}')])
        with patch.object(glm_loop, "post", post):
            data, usage = await glm_loop.caller("k", "glm-5.3-flash")("s", "p", self.SCHEMA)
        self.assertEqual(data["verdict"], "veto")
        self.assertEqual(usage["input_tokens"], 200)  # both attempts are charged
        retry = post.await_args_list[1].args[1]["messages"][-1]["content"]
        self.assertIn("answer.verdict must be one of", retry)
        self.assertIn("answer.reason is missing", retry)

    async def test_two_bad_answers_raise(self):
        post = AsyncMock(return_value=self.answer("not json"))
        with patch.object(glm_loop, "post", post):
            with self.assertRaisesRegex(ValueError, "did not match"):
                await glm_loop.caller("k", "glm-5.3-flash")("s", "p", self.SCHEMA)

    async def test_picture_reader_sends_the_image(self):
        post = AsyncMock(return_value=self.answer('{"lines": [{"n": 1, "text": "Rubber dinghy rapids"}]}'))
        with patch.object(glm_loop, "post", post):
            read = subtitle_contract.reader_for("glm-5.3-flash", "", "k")
            data, _ = await read("aGVsbG8=", [1])
        self.assertEqual(data["lines"][0]["text"], "Rubber dinghy rapids")
        content = post.await_args.args[1]["messages"][1]["content"]
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,aGVsbG8=")

    def test_schema_check_covers_nested_arrays_and_nullable_types(self):
        schema = subtitle_contract.READ_SCHEMA
        self.assertEqual(glm_loop.schema_errors({"lines": [{"n": 1, "text": "a"}]}, schema), [])
        self.assertEqual(glm_loop.schema_errors({"lines": [{"n": "1", "text": "a"}]}, schema),
                         ["answer.lines[0].n must be an integer"])
        self.assertEqual(glm_loop.schema_errors(None, {"type": ["string", "null"]}), [])
        self.assertTrue(glm_loop.schema_errors(3, {"type": ["string", "null"]}))


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, folder):
        from backend.agents.runtime import AgentRuntime
        from backend.agents.store import AgentStore

        runtime = AgentRuntime(AgentStore(folder), lambda: "")
        runtime._glm_key_getter = lambda: "zai-key"
        return runtime

    async def test_a_glm_session_calls_zai_with_its_profile(self):
        from backend.agents.models import AgentKind, AgentSession
        from backend.agents.runtime import AgentSpec

        with tempfile.TemporaryDirectory() as folder:
            runtime = self.runtime(folder)
            runtime.register(AgentSpec(kind=AgentKind.FETCH.value, model=lambda: "glm-5.3-flash",
                                       system=AsyncMock(return_value="orders"), tools=lambda s: []))
            session = AgentSession(agent=AgentKind.FETCH, model="glm-5.3-flash",
                                   messages=[{"role": "user", "content": "[wake] go"}])
            self.assertTrue(runtime.provider_ready("glm-5.3"))
            self.assertFalse(runtime.provider_ready("assistant-small"))
            with patch.object(glm_loop, "post", AsyncMock(return_value=TOOL_ANSWER)) as post:
                answer = await runtime._call_api(session, "orders", [])
        self.assertEqual(answer.content[1].name, "tmdb_lookup")
        key, body = post.await_args.args
        self.assertEqual(key, "zai-key")
        self.assertEqual(body["model"], "glm-5.3-flash")
        self.assertEqual(body["reasoning_effort"], "low")

    async def test_no_credit_is_not_retried_and_releases_the_hold(self):
        from backend.agents.models import AgentKind, AgentSession
        from backend.agents.runtime import AgentSpec

        refused = httpx.Response(429, json={"error": {"code": "1113", "message": "Insufficient balance"}})
        with tempfile.TemporaryDirectory() as folder:
            runtime = self.runtime(folder)
            spec = AgentSpec(kind=AgentKind.FETCH.value, model=lambda: "glm-5.3",
                             system=AsyncMock(return_value="orders"), tools=lambda s: [])
            runtime.register(spec)
            session = AgentSession(agent=AgentKind.FETCH, model="glm-5.3")
            runtime.store.save_session(session)

            class Client:
                def __init__(self, *a, **k):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                post = AsyncMock(return_value=refused)

            with patch.object(httpx, "AsyncClient", Client):
                with self.assertRaisesRegex(glm_loop.GLMStatusError, "no credit"):
                    await runtime._budgeted_call(session, "orders", [], spec)
            self.assertEqual(Client.post.await_count, 1)
            with runtime.store._connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM reasoning_reservations").fetchone()[0], 0)

    async def test_a_missing_key_says_which(self):
        from backend.agents.models import AgentKind, AgentSession

        with tempfile.TemporaryDirectory() as folder:
            runtime = self.runtime(folder)
            runtime._glm_key_getter = lambda: ""
            with self.assertRaisesRegex(glm_loop.GLMStatusError, "needs a Z.ai key"):
                await runtime._call_glm(AgentSession(agent=AgentKind.FETCH, model="glm-5.3"), "o", [])


class ConfigTests(unittest.TestCase):
    def test_key_is_saved_never_shown_and_counts_as_a_model_key(self):
        from backend.configuration import any_model_key, apply_config_update, effective_glm_key, public_config
        from backend.models import SparrowConfig

        with patch.dict(os.environ, {}, clear=False):
            for name in ("PROVIDER_API_KEY", "ALTPROVIDER_API_KEY", "ZAI_API_KEY"):
                os.environ.pop(name, None)
            config = SparrowConfig()
            self.assertFalse(any_model_key(config))
            config = apply_config_update(config, {"glm_api_key": " zai "})
            self.assertEqual(effective_glm_key(config), "zai")
            self.assertTrue(any_model_key(config))
            shown = public_config(config)
            self.assertEqual((shown["glm_api_key"], shown["glm_api_key_configured"]), ("", True))
            self.assertEqual(SparrowConfig.from_dict(config.to_dict()).glm_api_key, "zai")
            self.assertEqual(effective_glm_key(apply_config_update(config, {"clear_glm_api_key": True})), "")
            with patch.dict(os.environ, {"ZAI_API_KEY": "from-env"}):
                self.assertEqual(effective_glm_key(SparrowConfig()), "from-env")


class SubtitleDefaultsTests(unittest.TestCase):
    def test_a_zai_key_alone_puts_every_subtitle_role_on_glm(self):
        from backend.agents import subtitles

        with patch.object(subtitles, "effective_glm_key", lambda: "k"), \
                patch.object(subtitles, "effective_altprovider_key", lambda: ""), \
                patch.dict(os.environ, {}, clear=False):
            for name in ("SPARROW_SUBTITLE_MODEL", "SPARROW_SUBTITLE_CONTRACTOR", "SPARROW_SUBTITLE_VERIFIER"):
                os.environ.pop(name, None)
            self.assertEqual((subtitles.review_model(), subtitles.contractor_model(), subtitles.verifier_model(),
                              subtitles.reader_model()), ("glm-5.3", "glm-5.3-flash", "glm-5.3", "glm-5.3-flash"))
            with patch.dict(os.environ, {"SPARROW_SUBTITLE_CONTRACTOR": "glm-5.3"}):
                self.assertEqual(subtitles.reader_model(), "")  # 5.3 reads no pictures


if __name__ == "__main__":
    unittest.main()
