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
from .node_executor import NodeError, executable, probe_file, file_version, canonical
from .media_state import language_code


def model_path(data_dir):
    bundled = Path(getattr(sys, "_MEIPASS", data_dir)) / "models" / "whisper-base"
    return Path(os.getenv("SPARROW_TRANSCRIPTION_MODEL") or bundled)


async def candidates(executor, path, args):
    before = file_version(path)
    facts = await probe_file(path)
    out = []
    for track in facts["subtitle_tracks"]:
        if track["codec"] in ("subrip", "ass", "ssa", "webvtt", "mov_text", "text"):
            out.append(
                {
                    "id": "embedded:" + str(track["index"]),
                    "source": "embedded",
                    "index": track["index"],
                    "language": language_code(track["language"]),
                    "kind": (
                        "forced"
                        if track["forced"]
                        else "sdh" if track["hearing_impaired"] else "full"
                    ),
                    "title": track["title"] or "Included in this copy",
                }
            )
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
    folder = executor.cache_root / "subtitles" / identity
    folder.mkdir(parents=True, exist_ok=True)
    packet = {
        **{
            k: args[k] for k in ("text", "format", "version", "audio_index", "language")
        },
        "video": str(path),
        "folder": str(folder),
        "duration": facts["duration"],
        "ffmpeg": executable("ffmpeg"),
        "model_path": str(model_path(executor.data_dir)),
        "audio_language": language_code(
            next(
                t["language"]
                for t in facts["audio_tracks"]
                if t["index"] == audio_index
            )
        ),
    }
    if not packet["ffmpeg"]:
        raise NodeError("The packaged media tool is unavailable.")
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
