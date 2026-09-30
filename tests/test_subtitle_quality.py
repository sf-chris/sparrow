import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from backend.agents.media_state import file_version
from backend.agents.subtitle_quality import measure_correspondences
from backend.agents.subtitle_worker import process, inspect_evidence, sample_cues


def evidence():
    samples = []
    for index, start in enumerate([100, 300, 500, 700, 900]):
        samples.append(
            {
                "start": start - 10,
                "end": start + 20,
                "cue_index": index,
                "subtitle": {
                    "start": start + 0.1,
                    "end": start + 2.5,
                    "text": "Let's go home.",
                },
                "words": [
                    {"text": "家に", "start": start, "end": start + 1},
                    {"text": "帰ろう", "start": start + 1, "end": start + 2},
                ],
            }
        )
    return {"quality": {"passed": False, "structural_reasons": []}, "samples": samples}


def mappings(indices=(0, 2, 4)):
    return [
        {
            "sample": i,
            "first_word": 0,
            "last_word": 1,
            "explanation": "The Japanese phrase conveys going home.",
        }
        for i in indices
    ]


class SubtitleQualityTests(unittest.TestCase):
    def test_additional_samples_are_distributed_and_different(self):
        cues = [
            {"start": i * 10, "end": i * 10 + 2, "text": "A complete phrase."}
            for i in range(100)
        ]
        first = {i for i, _ in sample_cues(cues)}
        second = {i for i, _ in sample_cues(cues, 0.25)}
        self.assertEqual(len(second), 5)
        self.assertFalse(first & second)
        self.assertGreater(max(second) - min(second), 70)

    def test_quoted_source_phrase_locates_japanese_subwords_without_model_counting(
        self,
    ):
        matches = [
            {"sample": i, "source_text": "家に帰ろう。", "explanation": "Go home."}
            for i in (0, 2, 4)
        ]
        result = measure_correspondences(evidence(), matches)
        self.assertTrue(result["passed"])
        matches[0]["source_text"] = "A fabricated transcript"
        with self.assertRaises(ValueError):
            measure_correspondences(evidence(), matches)

    def test_translation_is_not_reported_as_zero_lexical_agreement(self):
        samples = evidence()["samples"]
        for sample in samples:
            sample["language"] = "ja"
        result = inspect_evidence([s["subtitle"] for s in samples], samples, 1000, "en")
        self.assertTrue(result["requires_correspondences"])
        self.assertTrue(result["reviewable"])
        self.assertNotIn("content_fraction", result)
        self.assertFalse(result["passed"])

    def test_bilingual_matches_measure_source_clock_and_tolerate_minor_gaps(self):
        result = measure_correspondences(evidence(), mappings())
        self.assertTrue(result["passed"])
        self.assertEqual(result["unmatched_samples"], [1, 3])
        self.assertEqual(result["median_timing_error"], 0.1)

    def test_large_offset_and_drift_cannot_be_approved(self):
        for drift in (False, True):
            value = evidence()
            for i, sample in enumerate(value["samples"]):
                for key in ("start", "end"):
                    sample["subtitle"][key] += i if drift else 4
            self.assertFalse(measure_correspondences(value, mappings())["passed"])

    def test_fabricated_boundaries_duplicates_and_one_scene_are_rejected(self):
        for proposed in [mappings((0, 0, 4)), mappings((0, 1, 2)), mappings()[:2]]:
            with self.assertRaises(ValueError):
                measure_correspondences(evidence(), proposed)
        proposed = mappings()
        proposed[0]["last_word"] = 50
        with self.assertRaises(ValueError):
            measure_correspondences(evidence(), proposed)
        value = evidence()
        value["quality"]["structural_reasons"] = ["Wrong duration"]
        with self.assertRaises(ValueError):
            measure_correspondences(value, mappings())

    def test_basic_preparation_keeps_timings_without_asr_or_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            video = folder / "movie.bin"
            video.write_bytes(b"fixture")
            packet = {
                "folder": str(folder / "subtitles"),
                "video": str(video),
                "version": file_version(video),
                "duration": 60,
                "text": "1\n00:00:05,000 --> 00:00:07,000\nHello there.\n",
                "format": "srt",
                "verify": False,
                "repair": False,
            }
            with (
                patch(
                    "backend.agents.subtitle_worker.transcribe_samples",
                    side_effect=AssertionError("No ASR"),
                ),
                patch(
                    "ffsubsync.ffsubsync.run",
                    side_effect=AssertionError("No alignment"),
                ),
            ):
                result = process(packet)
            self.assertTrue(result["unchanged"])
            self.assertFalse(result["quality"]["sync_checked"])
            self.assertEqual(result["samples"], [])
            self.assertIn(
                "00:00:05.000 --> 00:00:07.000",
                (folder / "subtitles/prepared.vtt").read_text(),
            )


class SubtitleSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_track_precedes_sparse_default_signs_using_actual_cues(self):
        from types import SimpleNamespace
        from backend.agents.subtitle_node import candidates

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "episode.mkv"
            path.write_bytes(b"fixture")
            tracks = [
                {
                    "index": i,
                    "codec": "subrip",
                    "language": "eng",
                    "forced": False,
                    "hearing_impaired": False,
                    "title": "Unreliable title",
                }
                for i in (2, 3)
            ]
            sign = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nSign\n"
            full = sign + b"\n00:00:05.000 --> 00:00:07.000\nReal dialogue here.\n"
            with (
                patch(
                    "backend.agents.subtitle_node.probe_file",
                    AsyncMock(return_value={"subtitle_tracks": tracks}),
                ),
                patch(
                    "backend.agents.subtitle_node.run_media",
                    AsyncMock(side_effect=[sign, full]),
                ),
            ):
                result = await candidates(
                    SimpleNamespace(roots={"library": Path(directory)}),
                    path,
                    {"root_id": "library"},
                )
            self.assertEqual([c["index"] for c in result["candidates"]], [3, 2])
            self.assertEqual([c["cue_count"] for c in result["candidates"]], [2, 1])
