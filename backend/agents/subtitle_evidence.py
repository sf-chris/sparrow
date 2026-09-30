"""Caption-independent speech evidence on the playback clock.

The worker decodes one audio stream once, maps speech activity across the whole
timeline and transcribes every detected speech region in its spoken language.
Utterances keep stable IDs, word timings and an acoustic onset measured by the
speech detector, so later tools can compare caption times with the voice.

Subtitle text is never an input: the evidence cannot be conditioned on the
captions it will be used to check. Work is checkpointed per chunk and resumes
after a restart or an elapsed time slice. Recognition can omit or mishear
speech; the saved limits say so and nothing here marks a track verified.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

SCHEMA = "sparrow-speech-evidence-1"
# Bump when saved outputs change meaning; evidence is keyed by it and rebuilt.
PIPELINE = 1
RATE = 16000
FRAME = 512  # One Silero VAD decision: 32 ms.
FRAME_SECONDS = FRAME / RATE
MAX_SECONDS = 6 * 3600
BLOCK_FRAMES = 56250  # 30 minutes of audio per speech-detector pass.

# Speech-detector thresholds for onsets and the speech map. The detector never
# decides what is transcribed: it misses quiet and music-covered dialogue.
STRICT = {"on": 0.5, "off": 0.35, "min_silence": 0.2, "min_speech": 0.1}
CHUNK_LIMIT = 28.0
QUIET_BEFORE_ONSET = 0.3
LANGUAGE_SWITCH_PROBABILITY = 0.8

# Whisper's well-known inventions over silence and music (subtitle credits and
# video-sign-offs from its training data). Flagged, never deleted.
PHANTOMS = (
    "ご視聴ありがとうございました",
    "チャンネル登録",
    "字幕",
    "thanksforwatching",
    "subtitlesby",
    "pleasesubscribe",
)

LIMITS = [
    "Speech recognition can omit, mishear or invent words; transcripts are evidence, not truth.",
    "Onsets come from a speech detector with 32 ms frames and are calibrated separately.",
    "The whole timeline is transcribed; text where no speech was detected is flagged, not removed.",
    "No person or model has listened to this audio; nothing here verifies a subtitle track.",
]


def save(path, value):
    path = Path(path)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf8"
    )
    os.replace(pending, path)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def evidence_key(version, audio_index, model, options):
    identity = {
        "pipeline": PIPELINE,
        "version": version,
        "audio_index": audio_index,
        "model": model,
        "options": options,
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode("utf8")
    ).hexdigest()[:32]


def model_identity(path):
    path = Path(path)
    weights = path / "model.bin"
    if not weights.is_file():
        raise ValueError("The speech model is not installed.")
    config = path / "config.json"
    return {
        "name": path.name,
        "weights_bytes": weights.stat().st_size,
        "config_sha256": (
            hashlib.sha256(config.read_bytes()).hexdigest() if config.is_file() else ""
        ),
    }


# ─── Speech activity ──────────────────────────────────────────────────────


def speech_regions(probabilities, *, on, off, min_silence, min_speech):
    """Hysteresis over per-frame speech probabilities → [(start, end)] seconds.

    Speech starts at the first frame at or above ``on`` and ends where the
    probability stays below ``off`` for ``min_silence`` seconds.
    """
    quiet_frames = max(1, math.ceil(min_silence / FRAME_SECONDS))
    regions, start, below = [], None, 0
    for index, value in enumerate(probabilities):
        if start is None:
            if value >= on:
                start, below = index, 0
            continue
        if value < off:
            below += 1
            if below >= quiet_frames:
                regions.append((start, index - below + 1))
                start, below = None, 0
        else:
            below = 0
    if start is not None:
        regions.append((start, len(probabilities) - below))
    return [
        (round(a * FRAME_SECONDS, 3), round(b * FRAME_SECONDS, 3))
        for a, b in regions
        if (b - a) * FRAME_SECONDS >= min_speech
    ]


def plan_chunks(duration, probabilities, *, limit=CHUNK_LIMIT):
    """Cut the whole timeline into ≤limit-second chunks at its quietest moments.

    Every second is transcribed: the speech detector misses quiet, whispered
    and music-covered dialogue, so it only chooses where to cut. Chunks are
    contiguous and never overlap, so each spoken word is transcribed once.
    """
    chunks, position = [], 0.0
    while duration - position > limit:
        first = int((position + 0.6 * limit) / FRAME_SECONDS)
        last = int((position + limit) / FRAME_SECONDS)
        # The loudest frame within ±96 ms, so a cut lands inside a pause
        # rather than on one quiet frame in the middle of a word.
        level = lambda i: max(probabilities[max(0, i - 3) : i + 4], default=0)
        cut = min(range(first, last), key=lambda i: (level(i), -i)) if first < last else last
        cut = round(max(position + 1, cut * FRAME_SECONDS), 3)
        chunks.append({"index": len(chunks), "start": position, "end": cut})
        position = cut
    chunks.append({"index": len(chunks), "start": position, "end": round(duration, 3)})
    return chunks


def uncovered(regions, duration, minimum=0.5):
    """Intervals of at least ``minimum`` seconds outside the given regions."""
    gaps, position = [], 0.0
    for start, end in regions:
        if start - position >= minimum:
            gaps.append([round(position, 3), start])
        position = max(position, end)
    if duration - position >= minimum:
        gaps.append([round(position, 3), round(duration, 3)])
    return gaps


def acoustic_onset(probabilities, around, *, floor=0.0, ceiling=None):
    """The detector's speech onset for an utterance, interpolated within a frame.

    Recogniser word starts are often early after a pause (calibration median
    about −0.16 s, up to −0.5 s), so the search reaches further forwards than
    back and takes the earliest rise into sustained speech. ``floor`` (the
    previous utterance's end) and ``ceiling`` (this utterance's end) stop it
    claiming a neighbour's onset. ``clean`` means the preceding
    QUIET_BEFORE_ONSET seconds contained no detected speech, so the onset is
    sharp enough to anchor timing.
    """
    on, off = STRICT["on"], STRICT["off"]
    lower = max(around - 0.4, floor)
    upper = around + 0.8 if ceiling is None else min(around + 0.8, ceiling)
    first = max(1, int(lower / FRAME_SECONDS))
    last = min(len(probabilities) - 1, int(upper / FRAME_SECONDS) + 1)
    best = None
    for index in range(first, last + 1):
        previous, value = probabilities[index - 1], probabilities[index]
        # The earliest rise into sustained speech: later rises within the
        # window are dips inside the same utterance, not its beginning.
        sustained = sum(v >= on for v in probabilities[index : index + 5]) >= 3
        if previous < on <= value and sustained:
            fraction = (on - previous) / (value - previous)
            moment = (index - 0.5 + fraction) * FRAME_SECONDS
            if lower <= moment <= upper:
                best = (moment, index)
                break
    if best is None:
        return None
    moment, index = best
    quiet = int(QUIET_BEFORE_ONSET / FRAME_SECONDS)
    preceding = probabilities[max(0, index - 1 - quiet) : index - 1]
    return {
        "time": round(moment, 3),
        "clean": len(preceding) == quiet and max(preceding, default=0) < off,
    }


def support(probabilities, start, end):
    first = max(0, int(start / FRAME_SECONDS))
    last = min(len(probabilities), max(first + 1, math.ceil(end / FRAME_SECONDS)))
    window = probabilities[first:last]
    return round(sum(v >= STRICT["on"] for v in window) / max(1, len(window)), 3)


# ─── Utterances ───────────────────────────────────────────────────────────


def split_segment(segment, probabilities, minimum_gap=0.25):
    """Split a recognised segment where the speaker actually pauses.

    Whisper segments can join several sentences or speakers. A word gap only
    splits when the speech detector also hears the pause, so imprecise word
    boundaries inside continuous speech do not create false onsets.
    """
    words = [w for w in segment["words"] if w[1] is not None]
    if not words:
        return [segment]
    parts, current = [], [words[0]]
    for word in words[1:]:
        gap_start, gap_end = current[-1][2], word[1]
        dip = False
        if gap_end - gap_start >= minimum_gap:
            first = int(gap_start / FRAME_SECONDS)
            last = max(first + 1, int(gap_end / FRAME_SECONDS))
            dip = min(probabilities[first:last], default=1) < STRICT["off"]
        if dip:
            parts.append(current)
            current = []
        current.append(word)
    parts.append(current)
    if len(parts) == 1:
        return [segment]
    return [
        {
            **segment,
            "text": "".join(w[0] for w in part).strip(),
            "start": part[0][1],
            "end": part[-1][2],
            "words": part,
            "split_from_segment": True,
        }
        for part in parts
    ]


def assemble(chunk_results, probabilities, duration):
    utterances = []
    for chunk in sorted(chunk_results, key=lambda c: c["start"]):
        for segment in chunk["segments"]:
            for part in split_segment(segment, probabilities):
                utterances.append((chunk, part))
    out, previous_text, previous_end = [], None, 0.0
    for number, (chunk, part) in enumerate(
        sorted(utterances, key=lambda item: item[1]["start"]), 1
    ):
        words = part["words"]
        timed = [w for w in words if w[1] is not None]
        start = timed[0][1] if timed else part["start"]
        end = timed[-1][2] if timed else part["end"]
        confidence = (
            round(sum(w[3] for w in timed) / len(timed), 3) if timed else None
        )
        onset = acoustic_onset(
            probabilities, start, floor=previous_end, ceiling=max(end, start + 0.1)
        )
        previous_end = max(previous_end, end)
        heard = support(probabilities, start, end)
        flags = []
        if not timed:
            flags.append("no_word_timings")
        if confidence is not None and confidence < 0.5:
            flags.append("low_confidence")
        if heard < 0.2:
            # Informational: the detector misses dialogue under music and
            # whispers, so this alone does not mean the words were invented.
            flags.append("speech_detector_silent")
        if part.get("compression_ratio", 0) > 2.4 or (
            part.get("no_speech_probability", 0) > 0.6
            and part.get("avg_logprob", 0) < -1.0
        ):
            flags.append("possible_hallucination")
        normalized = "".join(c for c in part["text"].casefold() if c.isalnum())
        if any(phantom in normalized for phantom in PHANTOMS):
            flags.append("possible_hallucination")
        if normalized and normalized == previous_text:
            flags.append("repeated_text")
        previous_text = normalized
        if chunk.get("language_source") == "detected_switch":
            flags.append("language_differs_from_audio_label")
        out.append(
            {
                "id": f"u{number:05d}",
                "start": round(start, 3),
                "end": round(end, 3),
                "onset": onset["time"] if onset else round(start, 3),
                "onset_source": "speech_detector" if onset else "recogniser",
                "clean_onset": bool(onset and onset["clean"]),
                "language": chunk["language"],
                "text": part["text"],
                "confidence": confidence,
                "speech_support": heard,
                "flags": flags,
                "chunk": chunk["index"],
                "words": [
                    [w[0], w[1], w[2], round(w[3], 3)] for w in words
                ],
            }
        )
    return out


# ─── Worker stages ────────────────────────────────────────────────────────


def extract(video, destination, audio_index, ffmpeg, ffprobe):
    """Decode onto the playback clock, preserving delayed starts and PTS gaps."""
    facts = json.loads(
        subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
            check=True,
            capture_output=True,
            timeout=120,
        ).stdout
    )
    track = next(
        (
            s
            for s in facts["streams"]
            if s.get("codec_type") == "audio" and s["index"] == audio_index
        ),
        None,
    )
    if track is None:
        raise ValueError("The selected stream is not an audio track.")
    origin = float(facts.get("format", {}).get("start_time") or 0)
    if not math.isfinite(origin):
        raise ValueError("The media has no usable start time.")
    partial = destination.with_name(destination.name + ".part")
    partial.unlink(missing_ok=True)
    subprocess.run(
        [
            ffmpeg, "-nostdin", "-v", "error", "-copyts", "-i", str(video),
            "-map", f"0:{audio_index}", "-vn", "-sn", "-dn",
            "-af", f"asetpts=PTS-({origin})/TB,aresample={RATE}:async=1:first_pts=0",
            "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le",
            "-t", str(MAX_SECONDS + 1), "-f", "wav", str(partial),
        ],
        check=True,
        capture_output=True,
        timeout=3600,
    )
    with wave.open(str(partial)) as audio:
        seconds = audio.getnframes() / audio.getframerate()
    if not 0 < seconds <= MAX_SECONDS:
        raise ValueError("Decoded audio is empty or longer than six hours.")
    os.replace(partial, destination)
    return {
        "audio_index": audio_index,
        "audio_tags": track.get("tags", {}),
        "container_start_seconds": origin,
        "audio_stream_start": track.get("start_time"),
        "audio_seconds": round(seconds, 3),
        "clock": "seconds from container playback start",
    }


def read_audio(path, start, end):
    import numpy as np

    with wave.open(str(path)) as audio:
        first = max(0, round(start * RATE))
        audio.setpos(min(first, audio.getnframes()))
        raw = audio.readframes(max(0, round((end - start) * RATE)))
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def detect_speech(path, seconds):
    import numpy as np
    from faster_whisper.vad import get_vad_model

    model = get_vad_model()
    values = []
    block = BLOCK_FRAMES * FRAME
    for start in range(0, math.ceil(seconds * RATE), block):
        samples = read_audio(path, start / RATE, (start + block) / RATE)
        if not len(samples):
            break
        samples = np.pad(samples, (0, (-len(samples)) % FRAME))
        values.extend(float(v) for v in model(samples).reshape(-1))
    frames = math.ceil(seconds / FRAME_SECONDS)
    return [round(v, 3) for v in values[:frames]]


def transcribe_chunk(model, audio, chunk, hint, options):
    samples = read_audio(audio, chunk["start"], chunk["end"])

    def run(language):
        segments, info = model.transcribe(samples, language=language, **options)
        return list(segments), info

    segments, info = run(hint)
    language, source = info.language, ("audio_label" if hint else "detected")
    detection = None
    words = [w for s in segments for w in (s.words or [])]
    doubtful = not words or (
        sum(w.probability for w in words) / len(words) < 0.45
        or all(s.avg_logprob < -1.0 for s in segments)
    )
    if hint and doubtful:
        # A labelled track can contain songs or scenes in another language.
        # Check only doubtful chunks: detection costs another encoder pass.
        detected, probability, _ = model.detect_language(samples)
        detection = {"language": detected, "probability": round(probability, 3)}
        if detected != hint and probability >= LANGUAGE_SWITCH_PROBABILITY:
            segments, info = run(detected)
            language, source = detected, "detected_switch"
    result = {
        "index": chunk["index"],
        "start": chunk["start"],
        "end": chunk["end"],
        "language": language,
        "language_source": source,
        "language_probability": round(info.language_probability, 3),
        "detection": detection,
        "segments": [],
    }
    offset = chunk["start"]
    for segment in segments:
        result["segments"].append(
            {
                "text": segment.text.strip(),
                "start": round(offset + segment.start, 3),
                "end": round(offset + segment.end, 3),
                "avg_logprob": round(segment.avg_logprob, 3),
                "no_speech_probability": round(segment.no_speech_prob, 3),
                "compression_ratio": round(segment.compression_ratio, 3),
                "words": [
                    [
                        w.word,
                        round(offset + w.start, 3) if math.isfinite(w.start) else None,
                        round(offset + w.end, 3) if math.isfinite(w.end) else None,
                        w.probability if math.isfinite(w.probability) else 0.0,
                    ]
                    for w in segment.words or []
                ],
            }
        )
    return result


def build(request, budget=None):
    """Advance the evidence for one request; return its status.

    Stages are idempotent. Chunks start only while the time budget allows,
    except the first, so every call makes progress.
    """
    from .media_state import file_version

    started = time.monotonic()
    folder = Path(request["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    video = Path(request["video"])
    if file_version(video) != request["version"]:
        raise ValueError("The video changed; its speech evidence must be rebuilt.")
    status_path = folder / "status.json"
    evidence_path = folder / "evidence.json"
    if evidence_path.is_file():
        return load(status_path)
    audio = folder / "audio.wav"
    if not (folder / "audio.json").is_file() or not audio.is_file():
        save(folder / "status.json", {"state": "extracting", "progress": 0})
        save(
            folder / "audio.json",
            extract(
                video, audio, request["audio_index"], request["ffmpeg"], request["ffprobe"]
            ),
        )
    source = load(folder / "audio.json")
    duration = source["audio_seconds"]
    if not (folder / "speech.json").is_file():
        save(folder / "status.json", {"state": "detecting_speech", "progress": 0})
        tick = time.monotonic()
        frames = bytes(min(255, round(v * 255)) for v in detect_speech(audio, duration))
        (folder / "speech.u8").write_bytes(frames)
        # Plan from the saved 8-bit values so later onset checks see the same data.
        probabilities = [v / 255 for v in frames]
        save(
            folder / "speech.json",
            {
                "strict": speech_regions(probabilities, **STRICT),
                "chunks": plan_chunks(duration, probabilities),
                "seconds": round(time.monotonic() - tick, 3),
            },
        )
    speech = load(folder / "speech.json")
    probabilities = [v / 255 for v in (folder / "speech.u8").read_bytes()]
    chunks = speech["chunks"]
    results_folder = folder / "chunks"
    results_folder.mkdir(exist_ok=True)
    remaining = [
        c for c in chunks if not (results_folder / f"c{c['index']:05d}.json").is_file()
    ]
    timings = load(folder / "timings.json") if (folder / "timings.json").is_file() else []
    if remaining:
        from faster_whisper import WhisperModel

        model = WhisperModel(
            request["model_path"],
            device="cpu",
            compute_type="int8",
            cpu_threads=request["threads"],
            num_workers=1,
            local_files_only=True,
        )
        if not model.model.is_multilingual:
            raise ValueError("Foreign-dialogue evidence needs a multilingual speech model.")
        for number, chunk in enumerate(remaining):
            elapsed = time.monotonic() - started
            average = sum(t["seconds"] for t in timings[-20:]) / max(1, len(timings[-20:]))
            if number and budget is not None and elapsed + 1.2 * average > budget:
                break
            tick = time.monotonic()
            result = transcribe_chunk(
                model, audio, chunk, request.get("language"), request["options"]
            )
            save(results_folder / f"c{chunk['index']:05d}.json", result)
            timings.append(
                {
                    "chunk": chunk["index"],
                    "audio_seconds": round(chunk["end"] - chunk["start"], 3),
                    "seconds": round(time.monotonic() - tick, 3),
                }
            )
            save(folder / "timings.json", timings)
            done = len(chunks) - len(remaining) + number + 1
            save(
                status_path,
                {
                    "state": "transcribing",
                    "progress": round(done / max(1, len(chunks)), 3),
                    "chunks_done": done,
                    "chunk_count": len(chunks),
                },
            )
        remaining = [
            c
            for c in chunks
            if not (results_folder / f"c{c['index']:05d}.json").is_file()
        ]
    if remaining:
        return load(status_path)
    if file_version(video) != request["version"]:
        raise ValueError("The video changed during transcription; evidence discarded.")
    results = [load(results_folder / f"c{c['index']:05d}.json") for c in chunks]
    utterances = assemble(results, probabilities, duration)
    strict = speech["strict"]
    speech_seconds = sum(b - a for a, b in strict)
    transcribed = sum(c["end"] - c["start"] for c in chunks)
    compute = sum(t["seconds"] for t in timings)
    languages = {}
    for utterance in utterances:
        languages[utterance["language"]] = languages.get(utterance["language"], 0) + 1
    evidence = {
        "schema": SCHEMA,
        "pipeline": PIPELINE,
        "media_version": request["version"],
        "source": source,
        "recogniser": {
            "engine": "faster-whisper",
            "model": request["model"],
            "compute": "cpu int8",
            "threads": request["threads"],
            "options": request["options"],
            "language_label": request.get("language"),
        },
        "speech_detector": {
            "engine": "silero-vad (bundled with faster-whisper)",
            "frame_seconds": FRAME_SECONDS,
            "thresholds": STRICT,
            "frames": "speech.u8",
        },
        "coverage": {
            "duration": duration,
            "speech_seconds": round(speech_seconds, 3),
            "transcribed_seconds": round(transcribed, 3),
            "chunk_count": len(chunks),
            "utterance_count": len(utterances),
            "clean_onsets": sum(u["clean_onset"] for u in utterances),
            "flagged_utterances": sum(bool(u["flags"]) for u in utterances),
            "languages": languages,
            "transcription_seconds": round(compute, 3),
            "real_time_factor": round(compute / max(transcribed, 1e-9), 3),
        },
        "speech": strict,
        "no_speech_detected": uncovered(strict, duration),
        "utterances": utterances,
        "limits": LIMITS,
        "verified": False,
    }
    save(evidence_path, evidence)
    status = {
        "state": "complete",
        "progress": 1,
        "chunks_done": len(chunks),
        "chunk_count": len(chunks),
        "coverage": evidence["coverage"],
    }
    save(status_path, status)
    return status


def listen(request):
    """Re-recognise one passage of saved evidence audio; nothing is replaced."""
    from faster_whisper import WhisperModel

    model = WhisperModel(
        request["model_path"],
        device="cpu",
        compute_type="int8",
        cpu_threads=request["threads"],
        num_workers=1,
        local_files_only=True,
    )
    chunk = {"index": 0, "start": request["start"], "end": request["end"]}
    result = transcribe_chunk(
        model, Path(request["audio"]), chunk, request.get("language"), request["options"]
    )
    probabilities = [v / 255 for v in Path(request["speech"]).read_bytes()]
    utterances = assemble([result], probabilities, request["duration"])
    return {
        "start": request["start"],
        "end": request["end"],
        "model": request["model"]["name"],
        "language": result["language"],
        "language_source": result["language_source"],
        "utterances": [
            {k: u[k] for k in ("start", "end", "onset", "onset_source", "clean_onset", "text", "confidence", "flags")}
            for u in utterances
        ],
    }


def main(path, budget=None):
    request_path = Path(path)
    folder = request_path.parent
    try:
        if hasattr(os, "nice"):
            os.nice(10)  # Playback and transcoding take priority over evidence.
        lock = open(folder / "worker.lock", "a")
        try:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass
        except OSError:
            raise ValueError("Speech evidence for this media is already being built.")
        request = load(request_path)
        value = listen(request) if request.get("kind") == "listen" else build(request, budget)
        result = {"ok": True, "value": value}
    except Exception as exc:
        result = {"ok": False, "error": (str(exc) or type(exc).__name__)[:2000]}
    save(folder / "result.json", result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(
        main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else None)
    )
