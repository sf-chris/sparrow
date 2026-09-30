"""Independent local speech evidence for the subtitle feasibility trial.

This is an opt-in diagnostic tool, not a production quality gate. Recognition
never sees the candidate captions. Every window (including empty results) is
saved; overlapping observations are retained rather than silently deduplicated.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
import subprocess
import time
import wave
from pathlib import Path

from .media_state import file_version
from .migrations import atomic_text
from .node_executor import executable, sha256_file

SAMPLE_RATE = 16000
MAX_SECONDS = 6 * 3600


def save(path, value):
    atomic_text(
        Path(path),
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )


def media_call(program, *args, timeout=120):
    binary = executable(program)
    if not binary:
        raise ValueError(
            f"Set SPARROW_{program.upper()} to the bundled {program} executable."
        )
    try:
        return subprocess.run(
            [binary, *map(str, args)],
            check=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"{program} failed: {exc.stderr.decode(errors='replace')[-2000:]}"
        ) from exc


def extract_audio(media, destination, audio_index=None):
    """Decode onto the playback clock, preserving delayed starts and PTS gaps."""
    media, destination = Path(media).resolve(strict=True), Path(destination)
    if destination.exists():
        raise ValueError(
            "Use a new audio destination; original files are never replaced."
        )
    before = file_version(media)
    facts = json.loads(
        media_call(
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            media,
        )
    )
    tracks = [s for s in facts["streams"] if s.get("codec_type") == "audio"]
    if audio_index is None:
        if len(tracks) != 1:
            choices = [{"index": s["index"], "tags": s.get("tags", {})} for s in tracks]
            raise ValueError(
                f"Choose --audio-index explicitly from these streams: {choices}"
            )
        audio_index = tracks[0]["index"]
    track = next((s for s in tracks if s["index"] == audio_index), None)
    if track is None:
        raise ValueError("The selected stream is not an audio track.")
    origin = float(facts.get("format", {}).get("start_time") or 0)
    duration = float(facts.get("format", {}).get("duration") or 0)
    if (
        not math.isfinite(origin)
        or not math.isfinite(duration)
        or duration > MAX_SECONDS
    ):
        raise ValueError("The trial supports finite media up to six hours.")
    media_call(
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-copyts",
        "-i",
        media,
        "-map",
        f"0:{audio_index}",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        f"asetpts=PTS-({origin})/TB,aresample={SAMPLE_RATE}:async=1:first_pts=0",
        "-ac",
        "1",
        "-ar",
        SAMPLE_RATE,
        "-c:a",
        "pcm_s16le",
        "-t",
        MAX_SECONDS + 1,
        "-n",
        destination,
        timeout=900,
    )
    with wave.open(str(destination)) as audio:
        audio_seconds = audio.getnframes() / audio.getframerate()
    if not 0 < audio_seconds <= MAX_SECONDS:
        raise ValueError("Decoded audio is empty or exceeds the six-hour trial limit.")
    identity = sha256_file(media)
    if file_version(media) != before:
        raise ValueError(
            "The input changed during extraction; do not use this evidence."
        )
    return {
        "path": str(media),
        "sha256": identity,
        "version": before,
        "audio_index": audio_index,
        "audio_tags": track.get("tags", {}),
        "container_start_seconds": origin,
        "audio_stream_start": track.get("start_time"),
        "audio_seconds": audio_seconds,
        "clock": "seconds from container playback start",
        "audio_sha256": sha256_file(destination),
        "probe": facts,
    }


def windows(start, end, width=25.0, overlap=5.0):
    if not all(math.isfinite(v) for v in (start, end, width, overlap)):
        raise ValueError("Window times must be finite.")
    if not 0 <= start < end or not 0 <= overlap < width:
        raise ValueError("Invalid audio window range.")
    position = start
    while position < end:
        finish = min(end, position + width)
        yield round(position, 6), round(finish, 6)
        if finish == end:
            break
        position += width - overlap


def transcribe(
    media,
    folder,
    model_path,
    *,
    audio_index=None,
    start=0.0,
    seconds=None,
    language=None,
    threads=2,
):
    """Save full, caption-independent observations; return an inspectable manifest."""
    import numpy as np
    from faster_whisper import WhisperModel

    folder, model_path = Path(folder), Path(model_path).resolve(strict=True)
    if not (model_path / "model.bin").is_file():
        raise ValueError(
            "Choose an installed multilingual faster-whisper model directory."
        )
    if (
        not math.isfinite(start)
        or start < 0
        or (seconds is not None and (not math.isfinite(seconds) or seconds <= 0))
    ):
        raise ValueError("Choose a nonnegative start and a positive duration.")
    if not 1 <= threads <= 8:
        raise ValueError("Use between one and eight CPU threads for the trial.")
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    started = time.monotonic()
    save(
        folder / "operation.json",
        {
            "state": "extracting",
            "media": str(Path(media).resolve()),
            "audio_index": audio_index,
        },
    )
    source = extract_audio(media, folder / "audio.wav", audio_index)
    end = (
        min(source["audio_seconds"], start + seconds)
        if seconds is not None
        else source["audio_seconds"]
    )
    ranges = list(windows(start, end))
    model_files = {
        str(p.relative_to(model_path)): sha256_file(p)
        for p in sorted(model_path.rglob("*"))
        if p.is_file()
    }
    options = {
        "task": "transcribe",
        "language": language,
        "word_timestamps": True,
        "beam_size": 5,
        "condition_on_previous_text": False,
        "vad_filter": False,
    }
    manifest = {
        "schema": "subtitle-audio-trial-1",
        "state": "transcribing",
        "source": source,
        "analysed_range": {"start": start, "end": end},
        "whole_audio_requested": start == 0 and seconds is None,
        "model": {
            "path": str(model_path),
            "files": model_files,
            "device": "cpu",
            "compute_type": "int8",
            "threads": threads,
        },
        "versions": {
            p: importlib.metadata.version(p) for p in ("faster-whisper", "ctranslate2")
        },
        "options": options,
        "window_count": len(ranges),
        "windows": [],
        "verified": False,
        "limits": [
            "ASR can omit or misrecognise speech.",
            "Window language detection can miss brief language switches.",
            "Overlapping windows contain duplicate observations.",
            "No independent listening or playback review has occurred.",
        ],
    }
    save(folder / "manifest.json", manifest)
    model = WhisperModel(
        str(model_path),
        device="cpu",
        compute_type="int8",
        cpu_threads=threads,
        num_workers=1,
        local_files_only=True,
    )
    if not model.model.is_multilingual:
        raise ValueError("An English-only model cannot test multilingual subtitles.")
    with wave.open(str(folder / "audio.wav")) as audio:
        for index, (first, last) in enumerate(ranges):
            name = f"window-{index:05d}.json"
            save(
                folder / "operation.json",
                {"state": "transcribing", "window": index, "start": first, "end": last},
            )
            audio.setpos(round(first * SAMPLE_RATE))
            samples = (
                np.frombuffer(
                    audio.readframes(round((last - first) * SAMPLE_RATE)), dtype="<i2"
                ).astype(np.float32)
                / 32768.0
            )
            tick = time.monotonic()
            segments, info = model.transcribe(samples, **options)
            result = {
                "index": index,
                "start": first,
                "end": last,
                "language": info.language,
                "language_probability": info.language_probability,
                "segments": [],
                "words": [],
            }
            for segment in segments:
                result["segments"].append(
                    {
                        "text": segment.text,
                        "start": first + segment.start,
                        "end": first + segment.end,
                        "avg_logprob": segment.avg_logprob,
                        "no_speech_probability": segment.no_speech_prob,
                    }
                )
                for word in segment.words or []:
                    valid = (
                        all(
                            math.isfinite(v)
                            for v in (word.start, word.end, word.probability)
                        )
                        and 0 <= word.start < word.end <= last - first + 0.05
                    )
                    result["words"].append(
                        {
                            "id": f"w{index:05d}-{len(result['words']):04d}",
                            "text": word.word.strip(),
                            "start": (
                                round(first + word.start, 3)
                                if math.isfinite(word.start)
                                else None
                            ),
                            "end": (
                                round(first + word.end, 3)
                                if math.isfinite(word.end)
                                else None
                            ),
                            "probability": (
                                word.probability
                                if math.isfinite(word.probability)
                                else None
                            ),
                            "usable_timing": bool(valid),
                        }
                    )
            result["processing_seconds"] = time.monotonic() - tick
            save(folder / name, result)
            manifest["windows"].append(
                {
                    "index": index,
                    "path": name,
                    "sha256": sha256_file(folder / name),
                    "start": first,
                    "end": last,
                    "language": info.language,
                    "word_count": len(result["words"]),
                }
            )
            save(folder / "manifest.json", manifest)
            print(
                f"Transcribed window {index + 1}/{len(ranges)} ({first:.1f}–{last:.1f}s, {info.language}, {len(result['words'])} words)",
                flush=True,
            )
    if file_version(Path(source["path"])) != source["version"]:
        raise ValueError("The input changed during transcription; discard this trial.")
    manifest.update(state="transcribed", processing_seconds=time.monotonic() - started)
    save(folder / "manifest.json", manifest)
    save(
        folder / "operation.json",
        {
            "state": "transcribed",
            "manifest_sha256": sha256_file(folder / "manifest.json"),
        },
    )
    return manifest


def load_words(folder, manifest):
    words = {}
    for entry in manifest["windows"]:
        path = Path(folder) / entry["path"]
        if sha256_file(path) != entry["sha256"]:
            raise ValueError("Speech evidence changed after transcription.")
        result = json.loads(path.read_text())
        words.update((w["id"], w) for w in result["words"])
    return words


def measure_matches(cues, words, matches):
    """Ground semantic proposals in real IDs; the caller supplies no timestamps."""
    by_cue = {c["id"]: c for c in cues}
    seen, seen_words, results = set(), set(), []
    for match in matches:
        if set(match) != {"cue_ids", "word_ids", "meaning", "explanation"}:
            raise ValueError(
                "Supply cue_ids, word_ids, meaning and explanation only; times come from evidence."
            )
        ids, word_ids = match["cue_ids"], match["word_ids"]
        if (
            not ids
            or len(set(ids)) != len(ids)
            or any(i not in by_cue or i in seen for i in ids)
        ):
            raise ValueError("Cue IDs must exist and appear in only one mapping.")
        if match["meaning"] not in {
            "equivalent",
            "different",
            "uncertain",
            "no_speech_match",
        }:
            raise ValueError("Unknown semantic judgement.")
        if (
            not isinstance(match["explanation"], str)
            or not match["explanation"].strip()
        ):
            raise ValueError(
                "Each mapping needs an explanation of meaning or uncertainty."
            )
        if len(set(word_ids)) != len(word_ids) or any(w not in words for w in word_ids):
            raise ValueError("Word IDs must refer to saved recognition evidence.")
        if match["meaning"] == "equivalent" and not word_ids:
            raise ValueError("Equivalent meaning needs spoken-word evidence.")
        spoken = [words[w] for w in word_ids]
        if spoken and (
            not spoken[0]["usable_timing"] or not spoken[-1]["usable_timing"]
        ):
            raise ValueError(
                "Phrase boundary words lack usable timings: "
                + ", ".join(
                    w["id"] + "=" + repr(w["text"])
                    for w in (spoken[0], spoken[-1])
                    if not w["usable_timing"]
                )
                + ". Retain interior untimed words, but use timed endpoints or another observation."
            )
        if spoken and len({w.split("-")[0] for w in word_ids}) != 1:
            raise ValueError(
                "Use one observed window per mapping; overlapping windows contain duplicates."
            )
        if spoken:
            indices = [int(w.rsplit("-", 1)[1]) for w in word_ids]
            if indices != list(range(indices[0], indices[-1] + 1)):
                raise ValueError(
                    "Cite a complete, ordered source phrase; do not skip intervening words. Selected IDs: "
                    + ", ".join(word_ids)
                )
            if seen_words.intersection(word_ids):
                raise ValueError(
                    "Group captions sharing an utterance into one mapping instead of reusing spoken words."
                )
        part = [by_cue[i] for i in ids]
        timed = [w for w in spoken if w["usable_timing"]]
        a, b = (
            (min(w["start"] for w in timed), max(w["end"] for w in timed))
            if spoken
            else (None, None)
        )
        results.append(
            {
                **match,
                "speech_start": a,
                "speech_end": b,
                "subtitle_start": min(c["start"] for c in part),
                "subtitle_end": max(c["end"] for c in part),
                "start_delta_seconds": (
                    round(min(c["start"] for c in part) - a, 3) if spoken else None
                ),
                "end_delta_seconds": (
                    round(max(c["end"] for c in part) - b, 3) if spoken else None
                ),
                "low_confidence_words": [
                    w["id"]
                    for w in spoken
                    if w["probability"] is None or w["probability"] < 0.5
                ],
                "untimed_interior_word_ids": [
                    w["id"] for w in spoken if not w["usable_timing"]
                ],
                "verified": False,
            }
        )
        seen.update(ids)
        seen_words.update(word_ids)
    return {
        "matches": results,
        "unreviewed_cue_ids": sorted(set(by_cue) - seen),
        "verified": False,
        "scope": "Semantic opinion about an ASR transcript; not independent audio or playback verification.",
    }
