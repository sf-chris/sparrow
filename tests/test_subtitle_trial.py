"""Trial boundaries; controlled language fixtures do not establish ASR accuracy."""

import json
import math
import asyncio
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.agents.node_executor import executable, sha256_file
from backend.agents.subtitle_audio import (
    extract_audio,
    load_words,
    measure_matches,
    media_call,
    save,
    windows,
)
from backend.agents.subtitle_trial import review
from test_discovery import response


class SubtitleCLITrialTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_cli_call_retains_unknown_usage_receipt(self):
        from backend.agents.subtitle_cli_trial import cli_caller

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = SimpleNamespace(
                pid=123456,
                returncode=None,
                communicate=AsyncMock(
                    side_effect=[asyncio.CancelledError(), (b"partial", b"")]
                ),
            )
            with (
                patch(
                    "backend.agents.subtitle_cli_trial.shutil.which",
                    return_value="/fixture/claude",
                ),
                patch(
                    "backend.agents.subtitle_cli_trial.asyncio.create_subprocess_exec",
                    return_value=process,
                ),
                patch("backend.agents.subtitle_cli_trial.os.killpg") as kill,
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await cli_caller(root)(
                        SimpleNamespace(model="fixture", messages=[]), "fixture", []
                    )
            kill.assert_called_once()
            receipt = json.loads(
                next((root / "cli-calls").glob("*.receipt.json")).read_text()
            )
            self.assertEqual(receipt["state"], "interrupted")
            self.assertIn("unknown", receipt["usage"])
            self.assertEqual(receipt["stdout"], "partial")

    async def test_cli_has_no_execution_tools_and_receipts_precede_tool_results(self):
        from backend.agents.subtitle_cli_trial import cli_caller
        from backend.agents.runtime import ToolDef

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "structured_output": {
                    "actions": [{"name": "inspect", "arguments": {"kind": "summary"}}]
                },
                "usage": {"input_tokens": 12},
            }
            process = SimpleNamespace(
                returncode=0,
                communicate=AsyncMock(return_value=(json.dumps(payload).encode(), b"")),
            )

            async def spawn(*args, **kwargs):
                self.assertEqual(
                    len(list((root / "cli-calls").glob("*.request.json"))), 1
                )
                for flag in (
                    "--safe-mode",
                    "--restricted",
                    "--strict-mcp-config",
                    "--no-session-persistence",
                ):
                    self.assertIn(flag, args)
                self.assertEqual(args[args.index("--tools") + 1], "")
                self.assertEqual(args[args.index("--setting-sources") + 1], "")
                self.assertNotIn(
                    "--bare", args
                )  # Existing login, never exported credentials.
                return process

            tool = ToolDef("inspect", "Read evidence", {"type": "object"}, AsyncMock())
            with (
                patch(
                    "backend.agents.subtitle_cli_trial.shutil.which",
                    return_value="/fixture/claude",
                ),
                patch(
                    "backend.agents.subtitle_cli_trial.asyncio.create_subprocess_exec",
                    side_effect=spawn,
                ),
            ):
                result = await cli_caller(root)(
                    SimpleNamespace(model="fixture", messages=[]), "fixture", [tool]
                )
            self.assertEqual(result.content[0].name, "inspect")
            self.assertEqual(result.usage.input_tokens, 12)
            self.assertEqual(len(list((root / "cli-calls").glob("*.receipt.json"))), 1)
            tool.handler.assert_not_called()  # The runtime, not the CLI, executes actions.

    async def test_unregistered_cli_actions_are_rejected_with_saved_receipt(self):
        from backend.agents.subtitle_cli_trial import cli_caller

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "structured_output": {"actions": [{"name": "publish", "arguments": {}}]}
            }
            process = SimpleNamespace(
                returncode=0,
                communicate=AsyncMock(return_value=(json.dumps(payload).encode(), b"")),
            )
            with (
                patch(
                    "backend.agents.subtitle_cli_trial.shutil.which",
                    return_value="/fixture/claude",
                ),
                patch(
                    "backend.agents.subtitle_cli_trial.asyncio.create_subprocess_exec",
                    return_value=process,
                ),
            ):
                with self.assertRaisesRegex(ValueError, "invalid tool batch"):
                    await cli_caller(root)(
                        SimpleNamespace(model="fixture", messages=[]), "fixture", []
                    )
            self.assertEqual(len(list((root / "cli-calls").glob("*.receipt.json"))), 1)


class SubtitleTrialTests(unittest.TestCase):
    def test_window_inventory_includes_silence_and_tail_independently_of_captions(self):
        spans = list(windows(17.5, 88.125))
        self.assertEqual(spans[0][0], 17.5)
        self.assertEqual(spans[-1][1], 88.125)
        self.assertTrue(all(b[0] < a[1] for a, b in zip(spans, spans[1:])))
        with self.assertRaises(ValueError):
            list(windows(0, float("nan")))

    def test_translated_phrases_use_source_word_times_not_model_supplied_times(self):
        cues, words, match = comparison_fixture()
        report = measure_matches(cues, words, [match])
        self.assertEqual(report["matches"][0]["start_delta_seconds"], 4)
        self.assertEqual(report["matches"][0]["end_delta_seconds"], 4)
        self.assertFalse(report["verified"])
        self.assertEqual(report["unreviewed_cue_ids"], ["c00001"])
        for invalid in (
            {**match, "start": 0},
            {**match, "word_ids": ["invented"]},
            {**match, "word_ids": []},
        ):
            with self.assertRaises(ValueError):
                measure_matches(cues, words, [invalid])

    def test_uncertainty_and_bad_word_timestamps_cannot_become_success(self):
        cues, words, match = comparison_fixture()
        report = measure_matches(
            cues, words, [{**match, "meaning": "uncertain", "word_ids": []}]
        )
        self.assertIsNone(report["matches"][0]["start_delta_seconds"])
        self.assertFalse(report["verified"])
        words["w00000-0001"]["usable_timing"] = False
        with self.assertRaises(ValueError):
            measure_matches(cues, words, [match])

    def test_repeated_cue_mapping_is_rejected(self):
        cues, words, match = comparison_fixture()
        with self.assertRaises(ValueError):
            measure_matches(cues, words, [match, match])

    def test_phrase_anchor_keeps_untimed_interior_word_without_inventing_its_timing(
        self,
    ):
        cues, words, match = comparison_fixture()
        words["w00000-0002"] = {**words["w00000-0001"], "id": "w00000-0002"}
        words["w00000-0001"] = {
            **words["w00000-0001"],
            "text": "not",
            "start": 10.5,
            "end": 10.5,
            "usable_timing": False,
        }
        match["word_ids"] = list(words)
        result = measure_matches(cues, words, [match])["matches"][0]
        self.assertEqual(result["untimed_interior_word_ids"], ["w00000-0001"])
        self.assertEqual(result["speech_start"], 10)
        self.assertEqual(result["speech_end"], 11)
        self.assertFalse(result["verified"])

    def test_word_mapping_cannot_skip_a_negation(self):
        cues, words, match = comparison_fixture()
        words["w00000-0002"] = {**words["w00000-0001"], "id": "w00000-0002"}
        match["word_ids"] = ["w00000-0000", "w00000-0002"]
        with self.assertRaisesRegex(ValueError, "skip"):
            measure_matches(cues, words, [match])

    def test_changed_audio_observation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root / "window.json", {"words": []})
            manifest = {
                "windows": [
                    {"path": "window.json", "sha256": sha256_file(root / "window.json")}
                ]
            }
            save(root / "window.json", {"words": [{"id": "forged"}]})
            with self.assertRaisesRegex(ValueError, "changed"):
                load_words(root, manifest)

    @unittest.skipUnless(
        executable("ffmpeg") and executable("ffprobe"), "requires bundled media tools"
    )
    def test_container_origin_and_delayed_audio_survive_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pcm = b"".join(
                struct.pack(
                    "<h", round(12000 * math.sin(2 * math.pi * 440 * n / 16000))
                )
                for n in range(6400)
            )
            with wave.open(str(root / "tone.wav"), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(pcm)
            media_call(
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=s=32x32:r=10:d=2",
                "-itsoffset",
                "0.6",
                "-i",
                root / "tone.wav",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-c:a",
                "pcm_s16le",
                "-output_ts_offset",
                "5",
                "-n",
                root / "delayed.mkv",
            )
            facts = extract_audio(root / "delayed.mkv", root / "decoded.wav")
            self.assertAlmostEqual(facts["container_start_seconds"], 5, places=2)
            with wave.open(str(root / "decoded.wav")) as stream:
                samples = struct.unpack(
                    "<" + "h" * stream.getnframes(),
                    stream.readframes(stream.getnframes()),
                )
            onset = (
                next(i for i, value in enumerate(samples) if abs(value) > 100) / 16000
            )
            self.assertAlmostEqual(onset, 0.6, delta=0.02)
            self.assertEqual(facts["audio_sha256"], sha256_file(root / "decoded.wav"))
            with self.assertRaisesRegex(ValueError, "never replaced"):
                extract_audio(root / "delayed.mkv", root / "decoded.wav")
            with self.assertRaisesRegex(ValueError, "not an audio"):
                extract_audio(root / "delayed.mkv", root / "another.wav", audio_index=0)


def comparison_fixture():
    # Constructed Arabic evidence: tests ID/timing mechanics, not translation quality.
    cues = [
        {"id": "c00000", "start": 14.0, "end": 15.0, "text": "Open the door."},
        {"id": "c00001", "start": 20.0, "end": 21.0, "text": "Unreviewed passage."},
    ]
    words = {
        "w00000-0000": {
            "id": "w00000-0000",
            "text": "افتح",
            "start": 10.0,
            "end": 10.5,
            "probability": 0.9,
            "usable_timing": True,
        },
        "w00000-0001": {
            "id": "w00000-0001",
            "text": "الباب",
            "start": 10.5,
            "end": 11.0,
            "probability": 0.9,
            "usable_timing": True,
        },
    }
    match = {
        "cue_ids": ["c00000"],
        "word_ids": list(words),
        "meaning": "equivalent",
        "explanation": "Constructed translation for the contract test.",
    }
    return cues, words, match


class SubtitleTrialRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_uses_durable_loop_and_resumes_without_repeating_completed_calls(
        self,
    ):
        cues, words, match = comparison_fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root / "window-00000.json", {"words": list(words.values())})
            save(
                root / "manifest.json",
                {
                    "state": "transcribed",
                    "analysed_range": {"start": 0, "end": 30},
                    "limits": ["Controlled fixture"],
                    "windows": [
                        {
                            "path": "window-00000.json",
                            "sha256": sha256_file(root / "window-00000.json"),
                        }
                    ],
                },
            )
            save(root / "candidate.json", {"language": "en", "cues": cues})
            call = AsyncMock(
                side_effect=[
                    response("inspect", {"kind": "summary"}),
                    response("inspect", {"kind": "window", "offset": 0}),
                    response("inspect", {"kind": "cues", "offset": 0}),
                    response(
                        "submit",
                        {
                            "matches": [match],
                            "notes": "Controlled diagnostic; unreviewed cue remains.",
                        },
                    ),
                ]
            )
            status = await review(
                root, "fixture-never-sent", "claude-haiku-4-5", call_api=call
            )
            self.assertEqual(status["state"], "completed")
            self.assertFalse(status["verified"])
            result = json.loads((root / "comparison.json").read_text())
            self.assertEqual(result["unreviewed_cue_ids"], ["c00001"])
            self.assertEqual(result["matches"][0]["start_delta_seconds"], 4)
            again = await review(
                root, "fixture-never-sent", "claude-haiku-4-5", call_api=call
            )
            self.assertEqual(again["session_id"], status["session_id"])
            self.assertEqual(call.await_count, 4)
            from backend.agents.store import AgentStore

            store = AgentStore(root / "review-state")
            with store._connect() as db:
                rows = db.execute(
                    "SELECT result FROM agent_invocations WHERE name IN ('inspect','submit')"
                ).fetchall()
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(r[0] is not None for r in rows))
            save(root / "candidate.json", {"language": "en", "cues": []})
            with self.assertRaisesRegex(ValueError, "changed"):
                await review(
                    root, "fixture-never-sent", "claude-haiku-4-5", call_api=call
                )
            self.assertEqual(call.await_count, 4)

    async def test_exhausted_budget_does_not_call_provider_or_write_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root / "manifest.json", {"state": "transcribed", "windows": []})
            save(root / "candidate.json", {"cues": []})
            call = AsyncMock()
            result = await review(
                root,
                "fixture-never-sent",
                "claude-haiku-4-5",
                max_dollars=0,
                call_api=call,
            )
            call.assert_not_called()
            self.assertFalse((root / "comparison.json").exists())
            self.assertEqual(result["state"], "budget_limited")
