"""Caption-independent speech evidence and caption timing measurement.

Constructed probabilities and fake recognisers test mechanics only. Measured
detector bias and professional timing come from subtitle_sync_calibration.py.
"""

import json
import math
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.agents import subtitle_evidence as speech
from backend.agents.node_executor import executable
from backend.agents.subtitle_sync import (
    ONSET_BIAS,
    TARGET_OFFSET,
    apply,
    correction,
    estimate,
)

F = speech.FRAME_SECONDS


def frames(spans, seconds):
    """Probabilities that are 0.95 inside the given (start, end) spans."""
    count = math.ceil(seconds / F)
    values = [0.02] * count
    for start, end in spans:
        for index in range(int(start / F), min(count, int(end / F))):
            values[index] = 0.95
    return values


def word(text, start, end, probability=0.9):
    return [text, start, end, probability]


class SpeechDetectorTests(unittest.TestCase):
    def test_regions_use_hysteresis_and_minimum_silence(self):
        values = frames([(1.0, 2.0), (2.1, 3.0), (5.0, 5.05), (6.0, 7.0)], 8)
        regions = speech.speech_regions(values, **speech.STRICT)
        # The 0.1 s gap is shorter than the 0.2 s minimum silence; the 32 ms
        # blip is shorter than the minimum speech duration.
        self.assertEqual(len(regions), 2)
        self.assertAlmostEqual(regions[0][0], 1.0, delta=F)
        self.assertAlmostEqual(regions[0][1], 3.0, delta=F)
        self.assertAlmostEqual(regions[1][0], 6.0, delta=F)

    def test_chunks_cover_the_whole_timeline_and_cut_in_pauses(self):
        duration = 100.0
        speech_spans = [(t, t + 3.0) for t in range(0, 100, 4)]
        values = frames(speech_spans, duration)
        chunks = speech.plan_chunks(duration, values)
        self.assertEqual(chunks[0]["start"], 0.0)
        self.assertEqual(chunks[-1]["end"], duration)
        for before, after in zip(chunks, chunks[1:]):
            self.assertEqual(before["end"], after["start"])
            # Every cut lands in one of the one-second pauses.
            self.assertTrue(any(e <= before["end"] <= e + 1 for _, e in speech_spans))
        self.assertTrue(all(c["end"] - c["start"] <= speech.CHUNK_LIMIT for c in chunks))
        # Silence is still transcribed: the detector misses real dialogue.
        silent = speech.plan_chunks(60.0, [0.0] * math.ceil(60 / F))
        self.assertEqual(silent[-1]["end"], 60.0)

    def test_onset_is_the_first_sustained_rise_within_the_utterance(self):
        values = frames([(10.0, 12.0)], 20)
        values[int(9.5 / F)] = 0.9  # a single-frame click is not an onset
        onset = speech.acoustic_onset(values, 9.8)
        self.assertAlmostEqual(onset["time"], 10.0, delta=F)
        self.assertTrue(onset["clean"])
        # Recogniser starts are usually early, sometimes slightly late.
        self.assertAlmostEqual(speech.acoustic_onset(values, 9.3)["time"], 10.0, delta=F)
        self.assertAlmostEqual(speech.acoustic_onset(values, 10.3)["time"], 10.0, delta=F)
        # Continuous speech before the onset makes it unusable as an anchor.
        joined = frames([(9.0, 9.9), (10.0, 12.0)], 20)
        self.assertFalse(speech.acoustic_onset(joined, 9.9, floor=9.9)["clean"])
        # A neighbour's onset before ``floor`` is never claimed.
        self.assertIsNone(speech.acoustic_onset(values, 10.2, floor=10.1))

    def test_segments_split_only_where_the_detector_hears_a_pause(self):
        values = frames([(1.0, 2.0), (2.6, 4.0)], 6)
        segment = {
            "text": "abc def",
            "start": 1.0,
            "end": 4.0,
            "words": [word("abc", 1.0, 1.9), word(" def", 2.6, 3.9)],
        }
        parts = speech.split_segment(segment, values)
        self.assertEqual([p["text"] for p in parts], ["abc", "def"])
        # The same word gap inside continuous detected speech does not split.
        continuous = frames([(1.0, 4.0)], 6)
        self.assertEqual(len(speech.split_segment(segment, continuous)), 1)


class AssemblyTests(unittest.TestCase):
    def test_utterances_keep_ids_onsets_and_honest_flags(self):
        values = frames([(10.0, 11.5), (20.0, 21.0)], 40)
        chunks = [
            {
                "index": 0,
                "start": 0.0,
                "end": 28.0,
                "language": "ja",
                "language_source": "audio_label",
                "segments": [
                    {
                        "text": "こんにちは",
                        "start": 9.8,
                        "end": 11.4,
                        "compression_ratio": 1.0,
                        "words": [word("こんにちは", 9.8, 11.4)],
                    },
                    {
                        "text": "はい",
                        "start": 20.0,
                        "end": 20.5,
                        "compression_ratio": 1.0,
                        "words": [word("はい", 20.0, 20.5, 0.3)],
                    },
                ],
            },
            {
                "index": 1,
                "start": 28.0,
                "end": 40.0,
                "language": "en",
                "language_source": "detected_switch",
                "segments": [
                    {
                        "text": "la la la",
                        "start": 30.0,
                        "end": 32.0,
                        "compression_ratio": 3.1,
                        "words": [word("la la la", 30.0, 32.0)],
                    }
                ],
            },
        ]
        utterances = speech.assemble(chunks, values, 40)
        self.assertEqual([u["id"] for u in utterances], ["u00001", "u00002", "u00003"])
        first, second, third = utterances
        self.assertEqual(first["onset_source"], "speech_detector")
        self.assertAlmostEqual(first["onset"], 10.0, delta=F)
        self.assertTrue(first["clean_onset"])
        self.assertEqual(first["flags"], [])
        self.assertIn("low_confidence", second["flags"])
        self.assertIn("speech_detector_silent", third["flags"])
        self.assertIn("possible_hallucination", third["flags"])
        self.assertIn("language_differs_from_audio_label", third["flags"])
        self.assertEqual(third["onset_source"], "recogniser")


class FakeModel:
    """Records calls; returns one segment per chunk in the requested language."""

    def __init__(self, *_, confidence=0.9, detected=("ja", 0.99), **__):
        self.calls, self.confidence, self.detected = [], confidence, detected
        self.model = SimpleNamespace(is_multilingual=True)

    def transcribe(self, samples, language=None, **options):
        self.calls.append(language)
        info = SimpleNamespace(language=language or "ja", language_probability=1.0)
        words = [SimpleNamespace(word="はい", start=1.0, end=1.4, probability=self.confidence)]
        segment = SimpleNamespace(
            text="はい",
            start=1.0,
            end=1.4,
            avg_logprob=-0.2 if self.confidence > 0.5 else -1.5,
            no_speech_prob=0.1,
            compression_ratio=1.0,
            words=words,
        )
        return iter([segment]), info

    def detect_language(self, samples):
        return self.detected[0], self.detected[1], []


class WorkerTests(unittest.TestCase):
    def request(self, root, video):
        from backend.agents.media_state import file_version

        return {
            "folder": str(root / "evidence"),
            "video": str(video),
            "version": file_version(video),
            "audio_index": 1,
            "language": "ja",
            "ffmpeg": "ffmpeg",
            "ffprobe": "ffprobe",
            "model_path": "unused",
            "model": {"name": "fake"},
            "threads": 2,
            "options": {"word_timestamps": True},
        }

    def test_doubtful_chunks_check_language_before_switching(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            with wave.open(str(audio), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(speech.RATE)
                stream.writeframes(b"\0\0" * speech.RATE * 3)
            chunk = {"index": 0, "start": 0.0, "end": 3.0}
            confident = FakeModel()
            result = speech.transcribe_chunk(confident, audio, chunk, "ja", {})
            self.assertEqual(confident.calls, ["ja"])
            self.assertEqual(result["language_source"], "audio_label")
            song = FakeModel(confidence=0.2, detected=("en", 0.95))
            result = speech.transcribe_chunk(song, audio, chunk, "ja", {})
            self.assertEqual(song.calls, ["ja", "en"])
            self.assertEqual(result["language"], "en")
            self.assertEqual(result["language_source"], "detected_switch")
            unsure = FakeModel(confidence=0.2, detected=("en", 0.5))
            result = speech.transcribe_chunk(unsure, audio, chunk, "ja", {})
            self.assertEqual(unsure.calls, ["ja"])
            self.assertEqual(result["language"], "ja")
            self.assertEqual(result["detection"], {"language": "en", "probability": 0.5})

    def test_time_budget_checkpoints_and_resumes_without_repeating_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video.mkv"
            video.write_bytes(b"video")
            request = self.request(root, video)
            duration = 90.0

            def extract(video, destination, *args):
                with wave.open(str(destination), "wb") as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(speech.RATE)
                    stream.writeframes(b"\0\0" * round(speech.RATE * duration))
                return {"audio_seconds": duration, "clock": "test"}

            probabilities = frames([(t, t + 2.0) for t in range(1, 90, 5)], duration)
            models = []

            def model(*args, **kwargs):
                models.append(FakeModel())
                return models[-1]

            ticks = iter(float(n) for n in range(1000))
            with (
                patch.object(speech, "extract", side_effect=extract),
                patch.object(speech, "detect_speech", return_value=probabilities),
                patch("faster_whisper.WhisperModel", side_effect=model),
                patch.object(speech.time, "monotonic", side_effect=lambda: next(ticks) * 30),
            ):
                first = speech.build(request, budget=60)
                self.assertEqual(first["state"], "transcribing")
                done = first["chunks_done"]
                self.assertLess(done, first["chunk_count"])
                final = speech.build(request, budget=None)
            self.assertEqual(final["state"], "complete")
            self.assertEqual(
                sum(len(m.calls) for m in models), final["chunk_count"]
            )
            evidence = json.loads((root / "evidence" / "evidence.json").read_text())
            self.assertEqual(evidence["schema"], speech.SCHEMA)
            self.assertFalse(evidence["verified"])
            self.assertEqual(evidence["coverage"]["transcribed_seconds"], duration)
            # Completed evidence is returned without loading a model again.
            with patch("faster_whisper.WhisperModel", side_effect=AssertionError):
                self.assertEqual(speech.build(request)["state"], "complete")
            video.write_bytes(b"changed video")
            with self.assertRaisesRegex(ValueError, "changed"):
                speech.build(request)

    @unittest.skipUnless(executable("ffmpeg") and executable("ffprobe"), "requires media tools")
    def test_extraction_keeps_the_playback_clock_for_delayed_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pcm = b"".join(
                struct.pack("<h", round(12000 * math.sin(2 * math.pi * 440 * n / 16000)))
                for n in range(6400)
            )
            with wave.open(str(root / "tone.wav"), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(pcm)
            subprocess.run(
                [
                    executable("ffmpeg"), "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i", "color=s=32x32:r=10:d=2",
                    "-itsoffset", "0.6", "-i", str(root / "tone.wav"),
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le",
                    "-output_ts_offset", "5", str(root / "delayed.mkv"),
                ],
                check=True,
            )
            facts = speech.extract(
                root / "delayed.mkv", root / "audio.wav", 1,
                executable("ffmpeg"), executable("ffprobe"),
            )
            self.assertAlmostEqual(facts["container_start_seconds"], 5, places=2)
            with wave.open(str(root / "audio.wav")) as stream:
                samples = struct.unpack(
                    "<" + "h" * stream.getnframes(), stream.readframes(stream.getnframes())
                )
            onset = next(i for i, v in enumerate(samples) if abs(v) > 100) / 16000
            self.assertAlmostEqual(onset, 0.6, delta=0.02)
            with self.assertRaisesRegex(ValueError, "not an audio"):
                speech.extract(
                    root / "delayed.mkv", root / "other.wav", 0,
                    executable("ffmpeg"), executable("ffprobe"),
                )


class ModelChoiceTests(unittest.TestCase):
    def test_medium_is_preferred_where_installed_and_base_is_the_fallback(self):
        import os
        from backend.agents.subtitle_node import evidence_model, evidence_threads

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, medium = root / "bundled" / "whisper-base", root / "data" / "models" / "whisper-medium"
            for folder in (base, medium):
                folder.mkdir(parents=True)
            (base / "model.bin").write_bytes(b"base")
            environment = {"SPARROW_TRANSCRIPTION_MODEL": str(base), "SPARROW_DATA_DIR": str(root / "data")}
            with patch.dict(os.environ, environment):
                os.environ.pop("SPARROW_EVIDENCE_MODEL", None)
                self.assertEqual(evidence_model(root / "data" / "local-node"), base)
                (medium / "model.bin").write_bytes(b"medium")
                self.assertEqual(evidence_model(root / "data" / "local-node"), medium)
                os.environ["SPARROW_EVIDENCE_MODEL"] = str(base)
                self.assertEqual(evidence_model(root / "data" / "local-node"), base)
        self.assertTrue(2 <= evidence_threads() <= 4)


class EvidenceReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_saved_evidence_is_readable_beside_media(self):
        import base64
        import secrets
        import time
        from backend.agents.node_executor import Executor, file_version

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "library").mkdir()
            executor = Executor(root / "node", {"library": str(root / "library")})
            folder = executor.cache_root / "subtitle-evidence" / ("b" * 32)
            folder.mkdir(parents=True)
            for name in ("evidence.json", "request.json"):
                (folder / name).write_text('{"secret": "path"}')

            async def read(path):
                return await executor.execute(
                    {
                        "id": secrets.token_hex(8),
                        "kind": "read",
                        "args": {
                            "root_id": "cache",
                            "path": path,
                            "version": file_version(executor.cache_root / path),
                            "offset": 0,
                            "length": 18,
                        },
                        "expires": time.time() + 60,
                    }
                )

            allowed = await read(f"subtitle-evidence/{'b' * 32}/evidence.json")
            self.assertTrue(allowed["ok"], allowed)
            self.assertEqual(base64.b64decode(allowed["value"]["bytes"]), b'{"secret": "path"}')
            refused = await read(f"subtitle-evidence/{'b' * 32}/request.json")
            self.assertFalse(refused["ok"])


class NodeEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """The real worker in a subprocess, with real English speech clips."""

    async def test_node_builds_resumable_evidence_on_the_playback_clock(self):
        import os
        import secrets
        import time
        from backend.agents.node_executor import Executor, run_media, file_version
        from backend.agents.subtitle_node import evidence_model, model_path

        if not executable("ffmpeg") or not executable("ffprobe"):
            self.skipTest("requires media tools")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if not evidence_model(root / "node"):
                self.skipTest("requires an installed speech model")
            library = root / "library"
            library.mkdir()
            clips = sorted((Path(__file__).parent / "fixtures/subtitles").glob("*.flac"))[:3]
            inputs = []
            for clip in clips:
                inputs += ["-i", str(clip)]
            # Two seconds of silence, then three speech clips back to back.
            parts = "".join(f"[{n + 1}:a]" for n in range(len(clips)))
            await run_media(
                "ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2",
                *inputs,
                "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=40",
                "-filter_complex",
                "[0:a]" + parts + f"concat=n={len(clips) + 1}:v=0:a=1[a]",
                "-map", f"{len(clips) + 1}:v", "-map", "[a]", "-shortest",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                "-metadata:s:a:0", "language=eng", "-y", str(library / "speech.mkv"),
                timeout=120,
            )
            executor = Executor(root / "node", {"library": str(library)})
            media = library / "speech.mkv"
            args = {
                "root_id": "library",
                "path": "speech.mkv",
                "version": file_version(media),
                "audio_index": 1,
                "budget": 60,
            }
            command = lambda: {
                "id": secrets.token_hex(12),
                "kind": "subtitle_evidence",
                "args": args,
                "expires": time.time() + 900,
            }
            result = await executor.execute(command())
            while result["ok"] and result["value"]["state"] != "complete":
                result = await executor.execute(command())
            self.assertTrue(result["ok"], result)
            value = result["value"]
            evidence = json.loads((executor.cache_root / value["path"]).read_text())
            self.assertEqual(evidence["source"]["audio_index"], 1)
            self.assertFalse(evidence["verified"])
            detected = [u for u in evidence["utterances"] if u["onset_source"] == "speech_detector"]
            self.assertGreaterEqual(len(detected), 2)
            self.assertTrue(all(u["language"] == "en" for u in evidence["utterances"]))
            # The first clip begins after the two seconds of leading silence.
            self.assertGreater(detected[0]["onset"], 2.0)
            # Completed evidence is reused rather than rebuilt.
            again = await executor.execute(command())
            self.assertEqual(again["value"]["version"], value["version"])
            media.write_bytes(media.read_bytes() + b"\0")
            changed = await executor.execute(command())
            self.assertFalse(changed["ok"])


def synthetic(onset_times):
    return {
        "utterances": [
            {
                "id": f"u{n:05d}",
                "onset": t,
                "onset_source": "speech_detector",
                "clean_onset": True,
            }
            for n, t in enumerate(onset_times)
        ]
    }


class SyncMeasurementTests(unittest.TestCase):
    def setUp(self):
        # Irregular dialogue: onsets 3–9 s apart across 20 minutes.
        times, t = [], 5.0
        while t < 1200:
            times.append(round(t, 3))
            t += 3 + (len(times) * 7919 % 600) / 100
        self.onsets = times
        # Detector onsets lag the voice; well-timed captions sit at the target.
        self.evidence = synthetic([t + ONSET_BIAS for t in times])
        jitter = lambda n: ((n * 37) % 9 - 4) / 100  # ±40 ms line noise
        self.cues = [
            {
                "start": t + TARGET_OFFSET + jitter(n),
                "end": t + TARGET_OFFSET + jitter(n) + 1.5,
                "text": "line",
            }
            for n, t in enumerate(times)
        ]

    def moved(self, function):
        return [
            {**c, "start": function(c["start"]), "end": function(c["end"])}
            for c in self.cues
        ]

    def test_good_track_needs_no_correction(self):
        measured = estimate(self.cues, self.evidence)
        self.assertTrue(measured["consistent"])
        self.assertLess(abs(measured["offset"] - TARGET_OFFSET), 0.02)
        self.assertIsNone(correction(measured))

    def test_constant_offset_is_measured_and_corrected(self):
        for shift in (-4.0, -0.3, 0.12, 2.5):
            measured = estimate(self.moved(lambda t: t + shift), self.evidence)
            self.assertAlmostEqual(measured["offset"] - TARGET_OFFSET, shift, delta=0.02)
            fixed = apply(self.moved(lambda t: t + shift), correction(measured))
            self.assertLess(abs(fixed[10]["start"] - self.cues[10]["start"]), 0.03)
        # Within tolerance nothing is rewritten: slightly late, or a lead-in.
        for shift in (0.03, -0.15):
            measured = estimate(self.moved(lambda t: t + shift), self.evidence)
            self.assertIsNone(correction(measured))
        # A consistently late track, like the measured Haibane release, is fixed.
        late = estimate(self.moved(lambda t: t + 0.22), self.evidence)
        self.assertEqual(correction(late)["kind"], "shift")
        self.assertAlmostEqual(correction(late)["seconds"], -0.22, delta=0.02)

    def test_frame_rate_drift_is_found_and_corrected(self):
        for ratio in (25 / (24000 / 1001), 1.001, 1 / 1.0005):
            track = self.moved(lambda t: t * ratio + 0.4)
            measured = estimate(track, self.evidence)
            fix = correction(measured)
            self.assertEqual(fix["kind"], "drift")
            fixed = apply(track, fix)
            for index in (5, len(fixed) // 2, len(fixed) - 5):
                self.assertLess(abs(fixed[index]["start"] - self.cues[index]["start"]), 0.05)

    def test_unrelated_track_is_inconsistent_and_never_corrected(self):
        other = [
            {"start": t * 1.37 % 1200, "end": t * 1.37 % 1200 + 1, "text": "x"}
            for t in self.onsets
        ]
        other.sort(key=lambda c: c["start"])
        measured = estimate(other, self.evidence)
        self.assertFalse(measured.get("consistent", False))
        self.assertIsNone(correction(measured))

    def test_too_few_onsets_is_unmeasurable_not_passing(self):
        measured = estimate(self.cues, synthetic([t + ONSET_BIAS for t in self.onsets[:5]]))
        self.assertFalse(measured["measurable"])
        self.assertIsNone(correction(measured))


if __name__ == "__main__":
    unittest.main()
