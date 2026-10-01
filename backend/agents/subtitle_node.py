"""Subtitle operations that execute next to media, within the node's roots."""

import asyncio
import hashlib
import json
import os
import re
import struct
import sys
import time
from pathlib import Path
from .node_executor import (
    NodeError,
    executable,
    probe_file,
    file_version,
    canonical,
    run_media,
)
from .media_state import language_code


def model_path(data_dir):
    bundled = Path(getattr(sys, "_MEIPASS", data_dir)) / "models" / "whisper-base"
    return Path(os.getenv("SPARROW_TRANSCRIPTION_MODEL") or bundled)


# Whole-episode dialogue evidence. On a real Japanese episode large-v3-turbo was
# the most accurate and about 2.5× faster than medium; medium is far better than
# the bundled base model, which remains the fallback so evidence still works
# (with its recorded model) where neither is installed.
EVIDENCE_OPTIONS = {
    "beam_size": 5,
    "word_timestamps": True,
    "condition_on_previous_text": False,
    "vad_filter": False,
    # Whisper retries doubtful decodes at rising temperatures. On music the
    # default six-step schedule cost up to 7× real time and invented text;
    # three steps keep one retry for hard dialogue at bounded cost.
    "temperature": [0.0, 0.3, 0.6],
    "best_of": 3,
}


EVIDENCE_MODELS = ("whisper-large-v3-turbo", "whisper-medium")


def _model_folders(data_dir, name):
    shared = os.getenv("SPARROW_DATA_DIR")
    return (
        *([Path(shared) / "models" / name] if shared else []),
        Path(data_dir) / "models" / name,
        model_path(data_dir).parent / name,  # packaged beside base
    )


def evidence_model(data_dir):
    configured = os.getenv("SPARROW_EVIDENCE_MODEL")
    for candidate in (
        *([Path(configured)] if configured else []),
        *(f for name in EVIDENCE_MODELS for f in _model_folders(data_dir, name)),
        model_path(data_dir),
    ):
        if (candidate / "model.bin").is_file():
            return candidate
    return None


def listen_model(data_dir, choice):
    """The re-listening model: the evidence model, another installed one, or large-v3.

    A second recogniser makes different mistakes, so disagreement is useful
    evidence; neither is treated as truth.
    """
    default = evidence_model(data_dir)
    if choice == "large":
        names = ("whisper-large-v3",)
    elif choice == "alternative":
        names = tuple(n for n in (*EVIDENCE_MODELS, "whisper-large-v3") if default is None or n != default.name)
    else:
        return default
    return next(
        (f for name in names for f in _model_folders(data_dir, name) if (f / "model.bin").is_file()),
        None,
    )


def evidence_threads():
    # A quarter of the machine, between two and four threads: fast enough for
    # background work without starving playback or transcoding.
    return max(2, min(4, (os.cpu_count() or 2) // 4))


async def evidence(executor, path, args):
    """Advance caption-independent speech evidence for one audio stream.

    Each call runs the worker for at most ``budget`` seconds of transcription
    and returns progress; checkpoints make the next call resume. Completed
    evidence is reused for as long as the media, stream and model match.
    """
    from .subtitle_evidence import evidence_key, load, model_identity

    if file_version(path) != args["version"]:
        raise NodeError("The video changed before its dialogue could be analysed.")
    facts = await probe_file(path)
    audio_index = args.get("audio_index")
    track = next((t for t in facts["audio_tracks"] if t["index"] == audio_index), None)
    if not track:
        raise NodeError("Choose an audio track from this video.")
    if facts["duration"] > 6 * 3600:
        raise NodeError("Dialogue analysis currently supports videos up to six hours.")
    model = evidence_model(executor.data_dir)
    if not model:
        raise NodeError(
            "The speech model is unavailable. Dialogue analysis resumes after the node's media components are installed."
        )
    identity = model_identity(model)
    key = evidence_key(args["version"], audio_index, identity, EVIDENCE_OPTIONS)
    folder = executor.cache_root / "subtitle-evidence" / key
    finished = folder / "evidence.json"
    if not finished.is_file():
        language = language_code(track["language"])
        folder.mkdir(parents=True, exist_ok=True)
        request = folder / "request.json"
        request.write_text(
            canonical(
                {
                    "folder": str(folder),
                    "video": str(path),
                    "version": args["version"],
                    "audio_index": audio_index,
                    "language": language if re.fullmatch("[a-z]{2}", language) else None,
                    "ffmpeg": executable("ffmpeg"),
                    "ffprobe": executable("ffprobe"),
                    "model_path": str(model),
                    "model": identity,
                    "threads": evidence_threads(),
                    "options": EVIDENCE_OPTIONS,
                }
            ),
            encoding="utf8",
        )
        budget = min(max(float(args.get("budget", 540)), 60.0), 840.0)
        command = (
            [sys.executable, "--subtitle-evidence", str(request), str(budget)]
            if getattr(sys, "frozen", False)
            else [
                sys.executable,
                "-m",
                "backend.agents.subtitle_evidence",
                str(request),
                str(budget),
            ]
        )
        lock = getattr(executor, "_evidence_lock", None)
        if lock is None:
            executor._evidence_lock = lock = asyncio.Lock()
        async with lock:
            result_path = folder / "result.json"
            result_path.unlink(missing_ok=True)
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                # Extraction and speech detection run before the first chunk,
                # so allow them time beyond the transcription budget.
                await asyncio.wait_for(process.wait(), timeout=budget + 600)
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                raise
            except asyncio.TimeoutError:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                if not result_path.is_file():
                    # Completed chunks are checkpointed; the next call resumes.
                    status = folder / "status.json"
                    return {
                        "state": "partial",
                        "progress": (
                            load(status).get("progress", 0) if status.is_file() else 0
                        ),
                    }
            if not result_path.is_file():
                raise NodeError(
                    "The speech worker stopped unexpectedly. Check the node's media components."
                )
            result = load(result_path)
            if not result["ok"]:
                raise NodeError(result["error"])
    status = load(folder / "status.json")
    if status["state"] != "complete" or not finished.is_file():
        return {"state": "partial", "progress": status.get("progress", 0)}
    if file_version(path) != args["version"]:
        raise NodeError("The video changed during dialogue analysis.")
    return {
        "state": "complete",
        "progress": 1,
        "path": f"subtitle-evidence/{key}/evidence.json",
        "version": file_version(finished),
        "model": identity["name"],
        "coverage": status["coverage"],
    }


async def subtitle_counts(path, indices):
    """Cue counts for text subtitle streams without decoding the file per track.

    Matroska muxers record NUMBER_OF_FRAMES statistics tags, which read
    instantly. Otherwise one counting pass covers every subtitle stream at
    once. Counts only guide selection; an unknown count is simply absent.
    """
    counts = {}
    if not indices:
        return counts
    try:
        tagged = json.loads(
            await run_media(
                "ffprobe", "-select_streams", "s", "-show_entries",
                "stream=index:stream_tags", "-of", "json", path, timeout=45,
            )
        )
    except (NodeError, ValueError, asyncio.TimeoutError):
        tagged = {}
    for stream in tagged.get("streams", []):
        tags = {k.upper(): str(v) for k, v in (stream.get("tags") or {}).items()}
        value = next(
            (v for k, v in sorted(tags.items()) if k.startswith("NUMBER_OF_FRAMES")),
            "",
        )
        if stream.get("index") in indices and value.isdigit():
            counts[stream["index"]] = int(value)
    if any(i not in counts for i in indices):
        try:
            counted = json.loads(
                await run_media(
                    "ffprobe", "-count_packets", "-select_streams", "s",
                    "-show_entries", "stream=index,nb_read_packets", "-of", "json",
                    path, timeout=240,
                )
            )
        except (NodeError, ValueError, asyncio.TimeoutError):
            counted = {}
        for stream in counted.get("streams", []):
            value = str(stream.get("nb_read_packets", ""))
            if stream.get("index") in indices and value.isdigit():
                counts.setdefault(stream["index"], int(value))
    return counts


SIGNS_TITLE = re.compile(r"\bforced\b|\bsigns?\b|\bs\s*&\s*s\b|\bsongs?\s*(&|and)\s*signs?\b", re.I)
CAPTIONS_TITLE = re.compile(r"\bsdh\b|\bcc\b|closed.?caption|hearing|\bhoh\b|\bdub(titles?)?\b", re.I)


ENGLISH_TITLE = re.compile(r"\benglish\b|\beng\b|\ben[-_](us|gb)\b", re.I)


def track_language(track):
    """The track's language, trusting a title that names English over a
    contradicting tag ("English Subtitles" released tagged jpn)."""
    language = language_code(track.get("language") or "")
    if language != "en" and ENGLISH_TITLE.search(track.get("title") or ""):
        return "en"
    return language


def track_kind(track, cues=0, fullest=0):
    """Forced (signs and songs), SDH (captions, often of an English dub) or full.

    Release tags are unreliable: "Signs & Songs" and "English [SDH]" tracks
    often carry no forced or hearing-impaired flag, a caption track's
    sound-effect lines make it look the most complete, and some releases flag
    their full dialogue track as forced. A flagged track with as many cues as
    the fullest track in the file is dialogue, not signs.
    """
    title = track.get("title") or ""
    complete = re.search(r"\bfull\b|dialog", title, re.I)
    if SIGNS_TITLE.search(title) and not complete:
        return "forced"
    if track.get("forced") and not (complete or (cues >= 100 and cues >= 0.6 * fullest)):
        return "forced"
    if track.get("hearing_impaired") or CAPTIONS_TITLE.search(title):
        return "sdh"
    return "full"


async def candidates(executor, path, args):
    before = file_version(path)
    facts = await probe_file(path)
    text = [
        track
        for track in facts["subtitle_tracks"]
        if track["codec"] in ("subrip", "ass", "ssa", "webvtt", "mov_text", "text")
    ]
    # The default track can contain only signs, even without a forced flag.
    # Cue counts steer selection towards full dialogue; they prove nothing.
    counts = await subtitle_counts(path, [t["index"] for t in text])
    fullest = max(counts.values(), default=0)
    out = [
        {
            "id": "embedded:" + str(track["index"]),
            "source": "embedded",
            "index": track["index"],
            "language": track_language(track),
            "kind": track_kind(track, counts.get(track["index"], 0), fullest),
            "title": track["title"] or "Included in this copy",
            "cue_count": counts.get(track["index"], 0),
        }
        for track in text
    ]
    for sidecar in sorted(path.parent.iterdir()):
        if not sidecar.name.startswith(
            path.stem + "."
        ) or sidecar.suffix.lower() not in (".srt", ".vtt", ".ass", ".ssa"):
            continue
        relative = sidecar.relative_to(executor.roots[args["root_id"]]).as_posix()
        executor.path(args["root_id"], relative)
        tags = sidecar.name[len(path.stem) + 1 :].lower().split(".")[:-1]
        language = next(
            (
                language_code(t)
                for t in tags
                if re.fullmatch("[a-z]{2,3}", t) and t not in ("sdh",)
            ),
            "und",
        )
        out.append(
            {
                "id": "sidecar:" + relative,
                "source": "sidecar",
                "path": relative,
                "version": file_version(sidecar),
                "language": language,
                "kind": (
                    "forced" if "forced" in tags else "sdh" if "sdh" in tags else "full"
                ),
                "title": sidecar.name,
            }
        )
    movie_hash = None
    if before["size_bytes"] >= 131072:
        with path.open("rb") as stream:
            first = stream.read(65536)
            stream.seek(-65536, 2)
            last = stream.read(65536)
        value = (
            before["size_bytes"]
            + sum(struct.unpack("<8192Q", first))
            + sum(struct.unpack("<8192Q", last))
        )
        movie_hash = f"{value & 0xffffffffffffffff:016x}"
    if before != file_version(path):
        raise NodeError("The media changed while subtitle sources were inspected.")
    # Measured cue counts are selection advice, never proof of sync or meaning.
    out.sort(key=lambda candidate: -candidate.get("cue_count", 0))
    return {"candidates": out, "movie_hash": movie_hash, "facts": facts}


async def prepare(executor, path, args):
    from .subtitle_worker import cues_from_text

    identity = args.get("task_id", "")
    if not re.fullmatch("[a-f0-9]{32}", identity):
        raise NodeError("Invalid subtitle operation.")
    if file_version(path) != args["version"]:
        raise NodeError("The video changed before subtitle repair.")
    facts = await probe_file(path)
    audio_index = args.get("audio_index")
    if not any(t["index"] == audio_index for t in facts["audio_tracks"]):
        raise NodeError("Choose an audio track from this video.")
    if facts["duration"] > 6 * 3600:
        raise NodeError(
            "Automatic subtitle repair currently supports videos up to six hours."
        )
    cues_from_text(args["text"], args["format"])
    correction = args.get("correction")
    if correction is not None and correction.get("kind") not in ("shift", "drift"):
        raise NodeError("Unsupported subtitle timing correction.")
    folder = executor.cache_root / "subtitles" / identity
    folder.mkdir(parents=True, exist_ok=True)
    packet = {
        **{
            k: args[k] for k in ("text", "format", "version", "audio_index", "language")
        },
        "video": str(path),
        "folder": str(folder),
        "duration": facts["duration"],
        "correction": correction,
    }
    request = folder / "request.json"
    request.write_text(canonical(packet), encoding="utf8")
    command = (
        [sys.executable, "--subtitle-worker", str(request)]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "backend.agents.subtitle_worker", str(request)]
    )
    lock = getattr(executor, "_subtitle_lock", None)
    if lock is None:
        executor._subtitle_lock = lock = asyncio.Lock()
    async with lock:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=900)
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise NodeError(
                "Subtitle preparation stopped before it could be verified. Retry when storage is ready."
            )
    result_path = folder / "result.json"
    if not result_path.is_file():
        raise NodeError(
            "The subtitle worker stopped unexpectedly. Check the node’s media components."
        )
    result = json.loads(result_path.read_text(encoding="utf8"))
    if not result["ok"]:
        raise NodeError(result["error"])
    if file_version(path) != args["version"]:
        raise NodeError("The video changed during subtitle repair.")
    value = result["value"]
    value.update(
        {
            "path": f"subtitles/{identity}/prepared.vtt",
            "original_path": f"subtitles/{identity}/original.vtt",
            "version": file_version(folder / "prepared.vtt"),
            "original_version": file_version(folder / "original.vtt"),
        }
    )
    # Avoid retaining a second source-video path and subtitle text in the request packet.
    request.unlink(missing_ok=True)
    return value


async def listen(executor, path, args):
    """Re-recognise one passage of saved evidence audio with other settings.

    The reviewer uses this for doubtful passages: another language hint, a
    larger model or a wider window. Results are new observations; the saved
    evidence never changes. Identical requests reuse their stored result.
    """
    from .subtitle_evidence import load, model_identity

    key = str(args.get("evidence", ""))
    if not re.fullmatch("[a-f0-9]{32}", key):
        raise NodeError("Invalid dialogue evidence.")
    folder = executor.cache_root / "subtitle-evidence" / key
    if not (folder / "evidence.json").is_file() or not (folder / "audio.wav").is_file():
        raise NodeError("That dialogue evidence is no longer stored. Analyse the episode again.")
    start, end = float(args["start"]), float(args["end"])
    duration = load(folder / "audio.json")["audio_seconds"]
    if not (0 <= start < end <= duration + 0.5) or end - start > 60:
        raise NodeError("Listen to at most 60 seconds of this episode at a time.")
    language = args.get("language")
    if language is not None and not re.fullmatch("[a-z]{2}", str(language)):
        raise NodeError("Use a two-letter language code, or none to detect it.")
    model = listen_model(executor.data_dir, args.get("model", "default"))
    if not model:
        raise NodeError("That speech model is not installed on this storage node; use default.")
    request = {
        "kind": "listen",
        "audio": str(folder / "audio.wav"),
        "speech": str(folder / "speech.u8"),
        "duration": duration,
        "start": round(start, 3),
        "end": round(end, 3),
        "language": language,
        "model_path": str(model),
        "model": model_identity(model),
        "threads": evidence_threads(),
        "options": EVIDENCE_OPTIONS,
    }
    identity = hashlib.sha256(canonical(request).encode()).hexdigest()[:32]
    work = folder / "listens" / identity
    work.mkdir(parents=True, exist_ok=True)
    result_path = work / "result.json"
    if not result_path.is_file():
        (work / "request.json").write_text(canonical(request), encoding="utf8")
        command = (
            [sys.executable, "--subtitle-evidence", str(work / "request.json")]
            if getattr(sys, "frozen", False)
            else [
                sys.executable,
                "-m",
                "backend.agents.subtitle_evidence",
                str(work / "request.json"),
            ]
        )
        lock = getattr(executor, "_listen_lock", None)
        if lock is None:
            executor._listen_lock = lock = asyncio.Lock()
        async with lock:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=600)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                raise
    if not result_path.is_file():
        raise NodeError("The speech worker stopped unexpectedly.")
    result = load(result_path)
    if not result["ok"]:
        result_path.unlink(missing_ok=True)
        raise NodeError(result["error"])
    return result["value"]
