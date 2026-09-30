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
