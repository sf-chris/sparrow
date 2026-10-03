"""Calibrate speech-onset timing used to measure and correct caption sync.

Absolute: licensed English speech clips are placed at known sample positions,
alone and over background noise. Each clip's true onset comes from its clean
waveform; the evidence worker's detector onsets are compared with it.

Professional baseline (optional): pairs of saved evidence.json and a human-timed
subtitle file show where good captions sit relative to measured onsets.

    SPARROW_FFMPEG=… SPARROW_FFPROBE=… .venv/bin/python tests/subtitle_sync_calibration.py \\
        MODEL_DIR OUTPUT_DIR [EVIDENCE.json SUBTITLE.srt]...

No network, paid model call or private media is required for the absolute part.
"""

import json
import sys
import wave
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np

from backend.agents.media_state import file_version
from backend.agents.node_executor import executable
from backend.agents.subtitle_evidence import RATE, build, model_identity
from backend.agents.subtitle_sync import estimate
from backend.agents.subtitle_worker import cues_from_text

FIXTURE = Path(__file__).parent / "fixtures/subtitles"
GAPS = [2.4, 3.2, 1.1, 4.5, 2.7, 1.6, 3.8, 2.1, 2.9, 1.8, 3.5, 2.2]


def clip(path):
    import subprocess

    raw = subprocess.run(
        [executable("ffmpeg"), "-v", "error", "-i", str(path), "-ac", "1",
         "-ar", str(RATE), "-f", "s16le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0


def true_onset(samples):
    """Where the voice begins: the rising edge of the first voiced syllable.

    Clips open with room tone or breaths 25–40 dB below the voice; neither
    is where a viewer hears the line begin. Find the first 50 ms held within
    20 dB of the clip's peak, then walk back up its rising edge while the
    level stays within 32 dB of the peak (a dip ends the walk).
    """
    size = RATE // 200
    frames = samples[: len(samples) // size * size].reshape(-1, size)
    level = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9)
    peak = np.percentile(level, 99)
    for index in range(len(level) - 10):
        if np.median(level[index : index + 10]) >= peak - 20:
            while index > 0 and level[index - 1] >= peak - 32:
                index -= 1
            return index * size / RATE
    return 0.0


def assemble(clips, noise_db, rounds=3):
    rng = np.random.default_rng(7)
    parts, truth, position = [], [], 0.0
    for round_number in range(rounds):
        for index, samples in enumerate(clips):
            gap = GAPS[(index + round_number * 5) % len(GAPS)]
            parts.append(np.zeros(round(gap * RATE)))
            position += gap
            truth.append(position + true_onset(samples))
            parts.append(samples)
            position += len(samples) / RATE
    parts.append(np.zeros(4 * RATE))
    audio = np.concatenate(parts)
    if noise_db is not None:
        # Pink-ish background: speech-band noise that never goes silent.
        white = rng.standard_normal(len(audio))
        pink = np.convolve(white, np.ones(8) / 8, mode="same")
        pink *= 10 ** (noise_db / 20) / (np.sqrt((pink**2).mean()) + 1e-12)
        audio = audio + pink
    return np.clip(audio, -1, 1), truth


def write_wav(path, audio):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes((audio * 32767).astype("<i2").tobytes())


def absolute(model, output):
    clips = [clip(p) for p in sorted(FIXTURE.glob("*.flac"))]
    results, pooled = {}, []
    for name, noise in (("clean", None), ("noise_-45dB", -45), ("noise_-35dB", -35)):
        folder = output / name
        folder.mkdir(parents=True, exist_ok=True)
        audio, truth = assemble(clips, noise)
        media = folder / "speech.wav"
        write_wav(media, audio)
        request = {
            "folder": str(folder / "evidence"),
            "video": str(media),
            "version": file_version(media),
            "audio_index": 0,
            "ffmpeg": executable("ffmpeg"),
            "ffprobe": executable("ffprobe"),
            "model_path": str(model),
            "model": model_identity(model),
            "threads": 4,
            "language": "en",
            "options": {
                "beam_size": 5,
                "word_timestamps": True,
                "condition_on_previous_text": False,
                "vad_filter": False,
            },
        }
        build(request)
        evidence = json.loads((folder / "evidence" / "evidence.json").read_text())
        detected = [u for u in evidence["utterances"] if u["onset_source"] == "speech_detector"]
        errors, recogniser = [], []
        for moment in truth:
            nearest = min(detected, key=lambda u: abs(u["onset"] - moment), default=None)
            if nearest and abs(nearest["onset"] - moment) < 0.5:
                errors.append(nearest["onset"] - moment)
            spoken = min(evidence["utterances"], key=lambda u: abs(u["start"] - moment))
            if abs(spoken["start"] - moment) < 1.0:
                recogniser.append(spoken["start"] - moment)
        pooled += errors
        results[name] = {
            "clips": len(truth),
            "detector_onsets_found": len(errors),
            "detector_bias": round(median(errors), 3) if errors else None,
            "detector_mean": round(mean(errors), 3) if errors else None,
            "detector_abs_p90": (
                round(float(np.percentile(np.abs(errors), 90)), 3) if errors else None
            ),
            "recogniser_bias": round(median(recogniser), 3) if recogniser else None,
            "recogniser_abs_p90": (
                round(float(np.percentile(np.abs(recogniser), 90)), 3)
                if recogniser
                else None
            ),
        }
    # Median with a percentile-bootstrap interval, pooled across conditions.
    rng = np.random.default_rng(11)
    samples = [
        float(np.median(rng.choice(pooled, len(pooled)))) for _ in range(2000)
    ]
    results["pooled"] = {
        "onsets": len(pooled),
        "detector_bias": round(float(np.median(pooled)), 3),
        "ci95": [
            round(float(np.percentile(samples, 2.5)), 3),
            round(float(np.percentile(samples, 97.5)), 3),
        ],
    }
    return results


def professional(pairs):
    results = []
    for evidence_path, subtitle_path in pairs:
        evidence = json.loads(Path(evidence_path).read_text())
        cues = cues_from_text(
            Path(subtitle_path).read_text(encoding="utf-8-sig"),
            Path(subtitle_path).suffix[1:].lower(),
        )
        measured = estimate(cues, evidence)
        results.append(
            {
                "subtitle": Path(subtitle_path).name,
                **{
                    k: measured.get(k)
                    for k in (
                        "consistent", "offset", "offset_ci95", "line_spread",
                        "drift_over_track", "drift_over_track_ci95",
                        "paired_captions", "caption_count", "anchor_match", "prominence",
                    )
                },
            }
        )
    return results


def main():
    model, output = Path(sys.argv[1]), Path(sys.argv[2])
    output.mkdir(parents=True, exist_ok=True)
    rest = sys.argv[3:]
    report = {
        "model": model.name,
        "absolute": absolute(model, output),
        "professional": professional(list(zip(rest[::2], rest[1::2]))),
    }
    (output / "calibration.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
