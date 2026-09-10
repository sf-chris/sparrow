"""Reproduce the labelled real-speech offset/drift/wrong-cut benchmark.

Run with SPARROW_FFMPEG, SPARROW_FFPROBE and SPARROW_TRANSCRIPTION_MODEL pointing
at the bundled programs/model. No network, paid model call or private media.
"""

import asyncio
import json
import os
import sys
import tempfile
import wave
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.agents.node_executor import executable, run_media, probe_file
from backend.agents.subtitle_worker import render, cues_from_text


async def main():
    fixture = Path(__file__).parent / "fixtures/subtitles"
    transcript = dict(
        line.split(" ", 1)
        for line in (fixture / "transcripts.txt").read_text().splitlines()
    )
    model = os.environ.get("SPARROW_TRANSCRIPTION_MODEL")
    if not model:
        raise ValueError(
            "Point SPARROW_TRANSCRIPTION_MODEL at the packaged Whisper base model."
        )
    with tempfile.TemporaryDirectory(prefix="sparrow-subtitles-") as temporary:
        root = Path(temporary)
        samples = []
        cues = []
        position = 0
        for index, source in enumerate(sorted(fixture.glob("*.flac"))):
            wav = root / (source.stem + ".wav")
            await run_media(
                "ffmpeg",
                "-v",
                "error",
                "-i",
                source,
                "-ac",
                "1",
                "-ar",
                "16000",
                "-y",
                wav,
            )
            gap = [2.4, 3.2, 1.1, 4.5, 2.7, 1.6, 3.8, 2.1][index]
            samples.append(b"\0\0" * round(gap * 16000))
            position += gap
            with wave.open(str(wav)) as stream:
                data = stream.readframes(stream.getnframes())
                duration = len(data) / 32000
            samples.append(data)
            cues.append(
                {
                    "start": position,
                    "end": position + duration,
                    "text": transcript[source.stem].capitalize(),
                }
            )
            position += duration
        samples.append(b"\0\0" * 16000 * 4)
        with wave.open(str(root / "speech.wav"), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b"".join(samples))
        await run_media(
            "ffmpeg",
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
            "-y",
            root / "speech.mp4",
        )
        facts = await probe_file(root / "speech.mp4")
        results = []
        for case in ("good", "offset", "drift", "wrong_cut", "wrong_text"):
            shifted = []
            for index, cue in enumerate(cues):
                shift = (
                    7
                    if case == "offset"
                    else (5 if case == "wrong_cut" and index >= 4 else 0)
                )
                scale = 1.042 if case == "drift" else 1
                shifted.append(
                    {
                        **cue,
                        "start": cue["start"] * scale + shift,
                        "end": cue["end"] * scale + shift,
                        "text": (
                            "We cannot find the red spaceship behind this distant mountain."
                            if case == "wrong_text"
                            else cue["text"]
                        ),
                    }
                )
            folder = root / case
            folder.mkdir()
            packet = {
                "video": str(root / "speech.mp4"),
                "folder": str(folder),
                "version": facts["version"],
                "duration": facts["duration"],
                "audio_index": facts["audio_tracks"][0]["index"],
                "audio_language": "en",
                "language": "en",
                "model_path": model,
                "ffmpeg": executable("ffmpeg"),
                "text": render(shifted),
                "format": "srt",
            }
            request = folder / "request.json"
            request.write_text(json.dumps(packet))
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "backend.agents.subtitle_worker",
                str(request),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), 900)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise
            result = json.loads((folder / "result.json").read_text())
            assert result["ok"], result
            value = result["value"]
            expected = case in ("good", "offset", "drift")
            assert value["quality"]["passed"] == expected, (case, value["quality"])
            if case == "good":
                assert value["unchanged"]
            repaired = cues_from_text((folder / "prepared.vtt").read_text(), "vtt")
            residual = [
                max(abs(a["start"] - b["start"]), abs(a["end"] - b["end"]))
                for a, b in zip(cues, repaired)
            ]
            results.append(
                {
                    "case": case,
                    "accepted": value["quality"]["passed"],
                    "unchanged": value["unchanged"],
                    "alignment": value["alignment"],
                    "quality": value["quality"],
                    "labelled_median_error": median(residual),
                    "labelled_max_error": max(residual),
                }
            )
            print(case, "passed", flush=True)
        destination = (
            Path(sys.argv[1])
            if len(sys.argv) > 1
            else Path("docs/product-validation/subtitle-benchmark.json")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(
                {
                    "fixture": "LibriSpeech test-clean, chapter 6930-75918; eight labelled clips",
                    "scope": "English same-language speech; five fixtures, not a multilingual or commercial-film quality claim",
                    "seconds": facts["duration"],
                    "results": results,
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    asyncio.run(main())
