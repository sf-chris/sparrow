"""Integrity, revision and uncertainty gates for experimental subtitle editing."""

import copy
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.agents.node_executor import sha256_file
from backend.agents.subtitle_audio import save
from backend.agents.subtitle_investigation import Investigation, expand_match
from backend.agents.subtitle_worker import cues_from_text


def fixture(root):
    audio = root / "audio.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x00\x00" * 16000 * 20)
    subtitle = root / "original.srt"
    subtitle.write_text(
        "1\n00:00:05,200 --> 00:00:06,200\nOpen the door.\n\n2\n00:00:06,800 --> 00:00:07,800\nThank you.\n\n3\n00:00:15,000 --> 00:00:16,000\nUnrelated scene.\n"
    )
    folder = root / "case"
    folder.mkdir()
    save(
        folder / "case.json",
        {
            "audio_path": str(audio),
            "audio_sha256": sha256_file(audio),
            "subtitle_path": str(subtitle),
            "subtitle_sha256": sha256_file(subtitle),
            "duration": 20,
            "models": {"fixture": {"path": str(root / "not-loaded")}},
            "scenes": [{"id": "door", "start": 3, "end": 9}],
        },
    )
    case = Investigation(folder)
    observation = {
        "audio_sha256": sha256_file(audio),
        "start": 3,
        "end": 9,
        "task": "transcribe",
        "caption_conditioned": False,
        "words": [
            {
                "id": "w00000-0000",
                "text": "Open",
                "start": 4.2,
                "end": 4.5,
                "probability": 0.9,
                "usable_timing": True,
            },
            {
                "id": "w00000-0001",
                "text": "the",
                "start": 4.5,
                "end": 4.6,
                "probability": 0.9,
                "usable_timing": True,
            },
            {
                "id": "w00000-0002",
                "text": "door",
                "start": 4.6,
                "end": 4.9,
                "probability": 0.9,
                "usable_timing": True,
            },
        ],
    }
    evidence = case.import_observation(observation, {"model": "controlled-fixture"})
    return case, evidence, observation


def edit(case, evidence):
    return {
        "revision": case.revision()["revision"],
        "edits": [
            {
                "cue_id": "c00000",
                "start": 4.1,
                "end": 5.1,
                "text": "Open the door.",
                "reason": "Controlled late-caption repair.",
                "evidence_ids": [evidence],
            }
        ],
    }


def assessment(case, evidence):
    return {
        "revision": case.revision()["revision"],
        "reviewed_cue_ids": [c["id"] for c in case.scene_cues("door")],
        "evidence_ids": [evidence],
        "outcome": "aligned",
        "meaning_review": "Controlled correspondence, not an ASR quality test.",
        "timing_review": "Controlled onset matches after repair.",
        "issues": [],
        "anchors": [
            {
                "observation_id": evidence,
                "match": {
                    "cue_ids": ["c00000"],
                    "word_ids": ["w00000-0000", "w00000-0001", "w00000-0002"],
                    "meaning": "equivalent",
                    "explanation": "Same words in the constructed evidence.",
                },
            }
        ],
    }


class InvestigationTests(unittest.TestCase):
    def test_compact_range_includes_every_intervening_word(self):
        with tempfile.TemporaryDirectory() as d:
            case, evidence, observation = fixture(Path(d))
            match = assessment(case, evidence)["anchors"][0]["match"]
            ids = match.pop("word_ids")
            match["word_range"] = [ids[0], ids[-1]]
            self.assertEqual(expand_match(match, observation)["word_ids"], ids)
            match["word_range"].reverse()
            with self.assertRaisesRegex(ValueError, "forward order"):
                expand_match(match, observation)

    def test_offset_cannot_be_approved_without_repair(self):
        with tempfile.TemporaryDirectory() as d:
            case, evidence, _ = fixture(Path(d))
            with self.assertRaisesRegex(ValueError, "exceed timing limits"):
                case.assess("door", assessment(case, evidence))
            self.assertFalse((case.folder / "scenes/door/assessment.json").exists())

    def test_committed_patch_recovers_missing_receipt_without_reapplying(self):
        with tempfile.TemporaryDirectory() as d:
            case, evidence, _ = fixture(Path(d))
            request = edit(case, evidence)
            result = case.patch("door", request)
            receipt = next((case.folder / "operations").glob("patch-*/receipt.json"))
            receipt.unlink()  # crash after the revision pointer, before receipt
            self.assertEqual(case.patch("door", request), result)
            self.assertEqual(len(list((case.folder / "revisions").iterdir())), 2)

    def test_patch_preserves_original_and_other_cues_and_replays_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            case, evidence, _ = fixture(root)
            original = (root / "original.srt").read_bytes()
            before = copy.deepcopy(case.revision())
            request = edit(case, evidence)
            result = case.patch("door", request)
            self.assertNotEqual(result["revision"], before["revision"])
            self.assertEqual(case.revision()["cues"][1:], before["cues"][1:])
            self.assertEqual((root / "original.srt").read_bytes(), original)
            self.assertEqual(case.patch("door", request), result)
            rendered = (
                case.folder / "revisions" / result["revision"] / "candidate.srt"
            ).read_text()
            self.assertEqual(cues_from_text(rendered, "srt")[0]["start"], 4.1)
            changed = copy.deepcopy(request)
            changed["edits"][0]["end"] = 5.2
            with self.assertRaisesRegex(ValueError, "draft changed"):
                case.patch("door", changed)

    def test_outside_scene_or_forged_evidence_cannot_edit_a_draft(self):
        with tempfile.TemporaryDirectory() as d:
            case, evidence, _ = fixture(Path(d))
            before = case.revision()["revision"]
            request = edit(case, evidence)
            request["edits"][0]["cue_id"] = "c00002"
            with self.assertRaisesRegex(ValueError, "current scene"):
                case.patch("door", request)
            request = edit(case, evidence)
            request["edits"][0]["evidence_ids"] = ["../../case.json"]
            with self.assertRaisesRegex(ValueError, "saved observation"):
                case.patch("door", request)
            self.assertEqual(case.revision()["revision"], before)

    def test_changed_sources_and_observations_invalidate_results(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            case, evidence, _ = fixture(root)
            path = case.folder / "observations" / (evidence + ".json")
            value = json.loads(path.read_text())
            value["words"][0]["start"] = 0
            save(path, value)
            with self.assertRaisesRegex(ValueError, "changed"):
                case.observation(evidence)
            (root / "original.srt").write_text("changed")
            with self.assertRaisesRegex(ValueError, "Original subtitle changed"):
                case.patch("door", edit(case, evidence))

    def test_scene_assessment_needs_current_complete_cues_and_grounded_anchors(self):
        with tempfile.TemporaryDirectory() as d:
            case, evidence, _ = fixture(Path(d))
            case.patch("door", edit(case, evidence))
            request = assessment(case, evidence)
            result = case.assess("door", request)
            self.assertFalse(result["verified"])
            self.assertEqual(result["measurements"][0]["start_delta_seconds"], -0.1)
            bad = copy.deepcopy(request)
            bad["reviewed_cue_ids"].pop()
            with self.assertRaisesRegex(ValueError, "every current cue"):
                case.assess("door", bad)
            bad = copy.deepcopy(request)
            bad["anchors"][0]["match"]["word_ids"] = ["invented"]
            with self.assertRaisesRegex(ValueError, "saved recognition"):
                case.assess("door", bad)
            bad = copy.deepcopy(request)
            bad["issues"] = ["Unresolved meaning"]
            with self.assertRaisesRegex(ValueError, "no unresolved"):
                case.assess("door", bad)
            change = edit(case, evidence)
            change["edits"][0]["end"] = 5.2
            case.patch("door", change)
            with self.assertRaisesRegex(ValueError, "stale"):
                case.assess("door", request)

    def test_translated_words_cannot_masquerade_as_original_word_times(self):
        with tempfile.TemporaryDirectory() as d:
            case, _, observation = fixture(Path(d))
            observation["task"] = "translate"
            evidence = case.import_observation(observation, {"model": "fixture"})
            with self.assertRaisesRegex(ValueError, "Translation timestamps"):
                case.assess("door", assessment(case, evidence))

    def test_caption_conditioning_is_not_independent_observation(self):
        with tempfile.TemporaryDirectory() as d:
            case, _, observation = fixture(Path(d))
            observation["caption_conditioned"] = True
            with self.assertRaisesRegex(ValueError, "Caption-conditioned"):
                case.import_observation(observation, {"model": "fixture"})


class InvestigationRecognitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_audio_range_never_starts_a_worker(self):
        with tempfile.TemporaryDirectory() as d:
            case, _, _ = fixture(Path(d))
            with patch(
                "backend.agents.subtitle_investigation.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as spawn:
                for start, end in [(-1, 4), (4, 50), (0, float("nan"))]:
                    with self.assertRaisesRegex(ValueError, "45 seconds"):
                        await case.recognize(
                            "door", {"model": "fixture", "start": start, "end": end}
                        )
                spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
