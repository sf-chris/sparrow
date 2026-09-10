"""Bounded, seekable browser conversion beside storage.

A static, authorized VOD playlist can request any segment. A contiguous ffmpeg
producer starts at the requested point and writes atomic segment files. A seek
beyond the prepared window restarts that producer at the new point.
"""

import asyncio
import re
import shutil
import time
import os
from pathlib import Path

from .node_executor import NodeError, executable, probe_file, file_version


class HLSCache:
    def __init__(self, cache_root):
        self.root = Path(cache_root) / "hls"
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs = {}
        self.lock = asyncio.Lock()

    def prune(self, keep=""):
        folders = []
        total = 0
        for folder in self.root.iterdir():
            if not folder.is_dir():
                continue
            size = sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
            total += size
            folders.append((folder.stat().st_mtime, folder, size))
        for modified, folder, size in sorted(folders):
            if folder.name == keep or folder.name in self.jobs:
                continue
            if time.time() - modified > 24 * 3600 or total > 8 * 1024**3:
                shutil.rmtree(folder)
                total -= size

    async def stop(self, session_id):
        job = self.jobs.pop(session_id, None)
        if job:
            process = job["process"]
            if process.returncode is None:
                process.kill()
                await process.wait()
            job["watchdog"].cancel()
        return {"stopped": True}

    async def segment(self, source, args):
        session_id = args.get("session_id", "")
        if not re.fullmatch("[a-f0-9]{32}", session_id):
            raise NodeError("Invalid playback session.")
        index = int(args.get("index", -1))
        if index < 0:
            raise NodeError("Invalid playback segment.")
        if file_version(source) != args["version"]:
            raise NodeError("The video changed after playback began.")
        folder = self.root / session_id
        folder.mkdir(exist_ok=True)
        os.utime(folder, None)
        self.prune(keep=session_id)
        segment = folder / f"{index:06d}.ts"
        if segment.exists():
            if session_id in self.jobs:
                self.jobs[session_id]["touched"] = time.monotonic()
            return {
                "path": f"hls/{session_id}/{index:06d}.ts",
                "version": file_version(segment),
            }
        async with self.lock:
            job = self.jobs.get(session_id)
            generated = [int(p.stem) for p in folder.glob("*.ts") if p.stem.isdigit()]
            latest = max(generated, default=job["first"] - 1 if job else -1)
            restart = (
                not job
                or job["process"].returncode is not None
                or index < job["first"]
                or index > latest + 2
            )
            if restart:
                if job:
                    await self.stop(session_id)
                active = [
                    j for j in self.jobs.values() if j["process"].returncode is None
                ]
                if len(active) >= 2:
                    raise NodeError(
                        "This node is already preparing two playback streams. Try again when one finishes."
                    )
                if shutil.disk_usage(folder).free < 512 * 1024 * 1024:
                    raise NodeError(
                        "This node needs at least 512 MB free to prepare playback."
                    )
                facts = await probe_file(source)
                if index * 6 >= facts["duration"]:
                    raise NodeError("Playback position is beyond this media.")
                audio = args.get("audio_index")
                if audio is not None and not any(
                    t["index"] == audio for t in facts["audio_tracks"]
                ):
                    raise NodeError("The chosen audio track does not exist.")
                command = [
                    executable("ffmpeg"),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    str(index * 6),
                    "-i",
                    str(source),
                    "-map",
                    f'0:{facts["video_index"]}',
                ]
                if audio is not None:
                    command.extend(
                        [
                            "-map",
                            f"0:{audio}",
                            "-c:a",
                            "aac",
                            "-b:a",
                            "160k",
                            "-ac",
                            "2",
                        ]
                    )
                else:
                    command.append("-an")
                command.extend(
                    [
                        "-sn",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "21",
                        "-threads",
                        "2",
                        "-vf",
                        "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                        "-pix_fmt",
                        "yuv420p",
                        "-r",
                        "24",
                        "-g",
                        "144",
                        "-keyint_min",
                        "144",
                        "-sc_threshold",
                        "0",
                        "-force_key_frames",
                        "expr:gte(t,n_forced*6)",
                        "-output_ts_offset",
                        str(index * 6),
                        "-f",
                        "hls",
                        "-hls_time",
                        "6",
                        "-hls_list_size",
                        "0",
                        "-hls_playlist_type",
                        "vod",
                        "-hls_flags",
                        "temp_file+independent_segments",
                        "-start_number",
                        str(index),
                        "-hls_segment_filename",
                        str(folder / "%06d.ts"),
                        "-y",
                        str(folder / "generated.m3u8"),
                    ]
                )
                if not command[0]:
                    raise NodeError("The packaged ffmpeg tool is unavailable.")
                log = open(folder / "conversion.log", "wb")
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=log,
                )
                log.close()
                job = {"process": process, "first": index, "touched": time.monotonic()}
                self.jobs[session_id] = job

                async def watchdog():
                    while process.returncode is None:
                        await asyncio.sleep(10)
                        # Stop abandoned playback and cap generated disk use.
                        size = sum(p.stat().st_size for p in folder.glob("*.ts"))
                        if (
                            time.monotonic() - job["touched"] > 60
                            or size > 8 * 1024**3
                            or shutil.disk_usage(folder).free < 128 * 1024**2
                        ):
                            process.kill()
                            await process.wait()
                            break
                    # Completed/abandoned producers must not hold cache entries
                    # forever. A later seek can start a fresh producer.
                    if self.jobs.get(session_id) is job:
                        self.jobs.pop(session_id, None)

                job["watchdog"] = asyncio.create_task(watchdog())
            job["touched"] = time.monotonic()
        for _ in range(900):
            if segment.exists():
                return {
                    "path": f"hls/{session_id}/{index:06d}.ts",
                    "version": file_version(segment),
                }
            if job["process"].returncode is not None:
                errors = (folder / "conversion.log").read_text(errors="replace")[-1000:]
                raise NodeError("The playback copy could not be prepared. " + errors)
            await asyncio.sleep(0.1)
        raise NodeError(
            "Preparing this playback segment took too long. Try again or choose a compatible copy."
        )
