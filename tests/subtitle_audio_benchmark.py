"""Reproduce the local audio trial on eight licensed recorded-speech fixtures.

Set SPARROW_FFMPEG, SPARROW_FFPROBE and SPARROW_TRANSCRIPTION_MODEL to installed
components. Run: .venv/bin/python tests/subtitle_audio_benchmark.py NEW_DIRECTORY
No network, Claude calls, movie downloads or quality certification occur.
"""

import json
import os
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.agents.subtitle_audio import media_call, save
from backend.agents.subtitle_trial import main as trial
from backend.agents.subtitle_worker import render


def main(destination):
    root = Path(destination)
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    fixture = Path(__file__).parent / "fixtures/subtitles"
    transcripts = dict(
        line.split(" ", 1)
        for line in (fixture / "transcripts.txt").read_text().splitlines()
    )
    chunks, cues, position = [], [], 0
    for index, source in enumerate(sorted(fixture.glob("*.flac"))):
        pcm = media_call(
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            source,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "pipe:1",
        )
        gap = [2.4, 3.2, 1.1, 4.5, 2.7, 1.6, 3.8, 2.1][index]
        chunks.append(b"\0\0" * round(gap * 16000))
        position += gap
        cues.append(
            {
                "id": f"c{index:05d}",
                "start": position,
                "end": position + len(pcm) / 32000,
                "text": transcripts[source.stem].capitalize(),
            }
        )
        chunks.append(pcm)
        position += len(pcm) / 32000
    chunks.append(b"\0\0" * 64000)
    with wave.open(str(root / "speech.wav"), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"".join(chunks))
    media_call(
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x240:r=24",
        "-i",
        root / "speech.wav",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-metadata:s:a:0",
        "language=eng",
        "-n",
        root / "speech.mp4",
    )
    (root / "good.srt").write_text(render(cues))
    (root / "late.srt").write_text(
        render([{**c, "start": c["start"] + 4, "end": c["end"] + 4} for c in cues])
    )
    save(
        root / "reference.json",
        {
            "source": "LibriSpeech test-clean; see tests/fixtures/subtitles/ATTRIBUTION.md",
            "cues": cues,
            "seconds": position + 4,
            "known_shift_seconds": 4,
            "timing_limit": "Cue boundaries are clip boundaries, not human-labelled word boundaries.",
        },
    )
    started = time.monotonic()
    trial(
        [
            "--media",
            str(root / "speech.mp4"),
            "--subtitles",
            str(root / "late.srt"),
            "--output",
            str(root / "trial"),
            "--model-path",
            os.environ["SPARROW_TRANSCRIPTION_MODEL"],
        ]
    )
    seconds = time.monotonic() - started
    manifest = json.loads((root / "trial/manifest.json").read_text())
    rss = None
    if sys.platform == "linux":
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    result = {
        "scope": "English recorded-speech feasibility only; not multilingual, movie or player acceptance.",
        "duration_seconds": manifest["source"]["audio_seconds"],
        "processing_seconds": seconds,
        "peak_process_rss_mib": rss,
        "window_count": len(manifest["windows"]),
        "word_observations": sum(e["word_count"] for e in manifest["windows"]),
        "live_semantic_review": False,
        "verified": False,
    }
    save(root / "results.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
