"""Portable, bounded media operations and a local receipt ledger.

No server-provided shell commands are accepted. Windows paths are interpreted
only on Windows; the coordinator uses root IDs and relative POSIX references.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from contextlib import contextmanager
from weakref import WeakValueDictionary

from .media_state import file_version

PROTOCOL = 1
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".m4v", ".mov", ".avi", ".webm", ".ts", ".wmv"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa", ".sub"}
READ_CHUNK = 1024 * 1024
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class NodeError(ValueError):
    pass


def executable(name):
    import sys

    bundled = (
        Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        / "bin"
        / (name + (".exe" if os.name == "nt" else ""))
    )
    configured = os.getenv("SPARROW_" + name.upper())
    return configured or (str(bundled) if bundled.is_file() else shutil.which(name))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_file(path):
    result = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


async def run_media(binary, *arguments, timeout=60):
    tool = executable(binary)
    if not tool:
        raise NodeError(
            f"{binary} is missing. Repair the Sparrow media-tools installation."
        )
    process = await asyncio.create_subprocess_exec(
        tool,
        "-nostdin" if binary == "ffmpeg" else "-v",
        *(["error"] if binary == "ffprobe" else []),
        *map(str, arguments),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, errors = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise NodeError(
            f'{binary} could not process this file: {errors.decode(errors="replace")[-1200:]}'
        )
    return output


async def probe_file(path):
    before = file_version(path)
    output = await run_media(
        "ffprobe", "-show_format", "-show_streams", "-of", "json", path, timeout=45
    )
    payload = json.loads(output)
    video = next(
        (
            s
            for s in payload.get("streams", [])
            if s["codec_type"] == "video"
            and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    duration = float(payload.get("format", {}).get("duration") or 0)
    if not video or duration <= 0:
        raise NodeError(
            "This file does not contain playable video with a known duration."
        )
    if before != file_version(path):
        raise NodeError(
            "The file changed while it was being inspected. Try again once it finishes writing."
        )

    def track(s):
        return {
            "index": s["index"],
            "codec": s.get("codec_name", ""),
            "language": s.get("tags", {}).get("language", "und"),
            "title": s.get("tags", {}).get("title", ""),
            "default": bool(s.get("disposition", {}).get("default")),
            "forced": bool(s.get("disposition", {}).get("forced")),
            "hearing_impaired": bool(s.get("disposition", {}).get("hearing_impaired")),
        }

    short_side = min(video.get("height", 0), video.get("width", 0))
    quality = (
        "2160p"
        if short_side >= 1600
        else "1080p" if short_side >= 800 else "720p" if short_side >= 570 else "480p"
    )
    audio = [track(s) for s in payload["streams"] if s["codec_type"] == "audio"]
    return {
        "version": before,
        "size_bytes": before["size_bytes"],
        "duration": duration,
        "width": video.get("width", 0),
        "height": video.get("height", 0),
        "video_codec": video.get("codec_name", ""),
        "video_index": video["index"],
        "format": payload.get("format", {}).get("format_name", ""),
        "quality": quality,
        "audio_tracks": audio,
        "audio_languages": [t["language"] for t in audio],
        "subtitle_tracks": [
            track(s) for s in payload["streams"] if s["codec_type"] == "subtitle"
        ],
    }


class Executor:
    def __init__(self, data_dir, roots, markers=None, downloader=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "node.db"
        self.roots = {
            key: Path(path).expanduser().resolve()
            for key, path in roots.items()
            if path
        }
        self.cache_root = self.data_dir / "cache"
        self.cache_root.mkdir(exist_ok=True)
        self.roots["cache"] = self.cache_root.resolve()
        self._hls_cache = None
        self.markers = markers or {}
        self.downloader = downloader
        self._mutation_lock = asyncio.Lock()
        self._operation_locks = WeakValueDictionary()
        self._last_prune = 0
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    result TEXT, state TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS job_revisions(id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def path(self, root_id, relative="", *, write=False):
        root = self.roots.get(root_id)
        if not root or not root.is_dir():
            raise NodeError("The selected storage folder is unavailable.")
        marker = self.markers.get(root_id)
        if marker:
            marker_path = root / ".sparrow-root-id"
            if not marker_path.is_file() or marker_path.read_text().strip() != marker:
                raise NodeError(
                    "The storage volume changed or is disconnected. Reconnect the original volume."
                )
        if not isinstance(relative, str) or "\\" in relative or "\x00" in relative:
            raise NodeError("Invalid relative media path.")
        posix, windows = PurePosixPath(relative), PureWindowsPath(relative)
        if (
            posix.is_absolute()
            or windows.drive
            or any(p in (".", "..") for p in posix.parts)
        ):
            raise NodeError("The path must stay inside its approved folder.")
        if any(
            ":" in p
            or p.endswith((" ", "."))
            or RESERVED.match(p)
            or p.startswith(".sparrow")
            for p in posix.parts
        ):
            raise NodeError("This filename is reserved or unsupported on Windows.")
        target = root.joinpath(*posix.parts)
        current = root
        for part in posix.parts:
            current = current / part
            try:
                info = current.lstat()
                if (
                    stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400
                ):
                    raise NodeError(
                        "Links and junctions cannot cross a storage boundary."
                    )
            except FileNotFoundError:
                if not write:
                    raise NodeError("This file or folder is unavailable.")
        resolved = target.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise NodeError("The path escapes the storage folder.")
        return resolved

    def capabilities(self):
        result = []
        for root_id in self.roots:
            if root_id == "cache":
                continue
            try:
                root = self.path(root_id)
                usage = shutil.disk_usage(root)
                result.append(
                    {
                        "id": root_id,
                        "available": True,
                        "free_bytes": usage.free,
                        "total_bytes": usage.total,
                        "writable": os.access(root, os.W_OK),
                    }
                )
            except (NodeError, OSError) as exc:
                result.append({"id": root_id, "available": False, "error": str(exc)})
        return {
            "protocol": PROTOCOL,
            "platform": __import__("platform").system(),
            "roots": result,
            "probe": bool(executable("ffprobe")),
            "transcode": bool(executable("ffmpeg")),
            "subtitle_model": (
                __import__(
                    "backend.agents.subtitle_node", fromlist=["model_path"]
                ).model_path(self.data_dir)
                / "model.bin"
            ).is_file(),
            "download": bool(self.downloader and self.downloader.get("type") != "none"),
        }

    def pending_results(self):
        with self.db() as db:
            if time.time() - self._last_prune > 300:
                db.execute(
                    "DELETE FROM operations WHERE delivered=1 AND created<? AND json_extract(payload,'$.kind') IN ('read','stat','hls_segment','subtitle_extract')",
                    (time.time() - 86400,),
                )
                self._last_prune = time.time()
            return [
                {"id": r["id"], "result": json.loads(r["result"])}
                for r in db.execute(
                    "SELECT id,result FROM operations WHERE state='done' AND delivered=0 LIMIT 8"
                )
            ]

    async def shutdown(self):
        if self._hls_cache:
            for identity in list(self._hls_cache.jobs):
                await self._hls_cache.stop(identity)

    async def stop_owned_downloads(self):
        if not self.downloader or self.downloader.get("type") == "none":
            return
        with self.db() as db:
            rows = db.execute(
                "SELECT payload FROM operations WHERE json_extract(payload,'$.kind')='download_add'"
            ).fetchall()
        for row in rows:
            args = json.loads(row["payload"]).get("args", {})
            try:
                await self._download("download_stop", {"hash": args["info_hash"]})
            except (NodeError, OSError):
                pass

    def delivered(self, operation_id):
        with self.db() as db:
            db.execute("UPDATE operations SET delivered=1 WHERE id=?", (operation_id,))
            # The coordinator acknowledged receipt. Preserve the operation identity,
            # but do not retain entire streamed movies in the node database.
            db.execute(
                "UPDATE operations SET result=? WHERE id=? AND json_extract(payload,'$.kind') IN ('read','stat','hls_segment','subtitle_extract')",
                (
                    canonical(
                        {
                            "ok": False,
                            "error": "This transient result was delivered. Issue a fresh read.",
                        }
                    ),
                    operation_id,
                ),
            )

    async def execute(self, command):
        operation_id = command["id"]
        if not re.fullmatch("[a-zA-Z0-9_-]{1,100}", operation_id):
            raise NodeError("Invalid operation ID.")
        payload = canonical(
            {k: command.get(k) for k in ("kind", "args", "job_id", "revision")}
        )
        lock = self._operation_locks.setdefault(operation_id, asyncio.Lock())
        async with lock:
            with self.db() as db:
                row = db.execute(
                    "SELECT * FROM operations WHERE id=?", (operation_id,)
                ).fetchone()
                if row:
                    if row["payload"] != payload:
                        raise NodeError(
                            "An operation ID cannot be reused with different arguments."
                        )
                    if row["state"] == "done":
                        return json.loads(row["result"])
                else:
                    db.execute(
                        "INSERT INTO operations VALUES (?, ?, NULL, 'running', 0, ?)",
                        (operation_id, payload, time.time()),
                    )
            try:
                if command.get("expires", 0) < time.time():
                    raise NodeError("Operation authority has expired.")
                job_id, revision = command.get("job_id"), int(
                    command.get("revision") or 0
                )
                if job_id:
                    with self.db() as db:
                        old = db.execute(
                            "SELECT revision FROM job_revisions WHERE id=?", (job_id,)
                        ).fetchone()
                        if old and revision < old["revision"]:
                            raise NodeError(
                                "This request revision is no longer authorised."
                            )
                        db.execute(
                            "INSERT INTO job_revisions VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET revision=MAX(revision,excluded.revision)",
                            (job_id, revision),
                        )
                kind, args = command["kind"], command.get("args") or {}
                if kind in (
                    "publish",
                    "delete_staging",
                    "download_add",
                    "download_remove",
                    "download_stop",
                    "download_start",
                ):
                    async with self._mutation_lock:
                        if command.get("expires", 0) < time.time():
                            raise NodeError("Operation authority has expired.")
                        if job_id:
                            with self.db() as db:
                                latest = db.execute(
                                    "SELECT revision FROM job_revisions WHERE id=?",
                                    (job_id,),
                                ).fetchone()
                            if latest and revision < latest["revision"]:
                                raise NodeError(
                                    "This request revision was superseded while waiting."
                                )
                        value = await self._dispatch(kind, args, operation_id)
                else:
                    value = await self._dispatch(kind, args, operation_id)
                result = {"ok": True, "value": value}
            except (NodeError, OSError, ValueError, asyncio.TimeoutError) as exc:
                result = {
                    "ok": False,
                    "error": str(exc) or "Media operation timed out.",
                }
            with self.db() as db:
                db.execute(
                    "UPDATE operations SET state='done', result=? WHERE id=?",
                    (canonical(result), operation_id),
                )
            return result

    async def _dispatch(self, kind, args, operation_id):
        if kind == "capabilities":
            return self.capabilities()
        if kind == "hls_stop":
            return (
                await self._hls_cache.stop(args["session_id"])
                if self._hls_cache
                else {"stopped": True}
            )
        if kind.startswith("download_"):
            return await self._download(kind, args)
        root_id = args.get("root_id", "library")
        if kind == "list":
            root = self.path(root_id, args.get("path", ""))
            if root.is_file():
                files = [root]
            else:
                # Never follow symlink/reparse directories, including outside the root.
                files = []
                for directory, dirs, names in os.walk(root, followlinks=False):
                    dirs[:] = [
                        d
                        for d in dirs
                        if not d.startswith(".")
                        and not (Path(directory) / d).is_symlink()
                        and not getattr(
                            (Path(directory) / d).lstat(), "st_file_attributes", 0
                        )
                        & 0x400
                    ]
                    for name in names:
                        candidate = Path(directory) / name
                        if (
                            candidate.suffix.lower() in VIDEO_EXTENSIONS
                            and not candidate.is_symlink()
                        ):
                            files.append(candidate)
                        if len(files) > 10000:
                            raise NodeError(
                                "This scan exceeds 10,000 files. Choose a smaller subfolder."
                            )
            out = []
            for path in sorted(files):
                relative = path.relative_to(self.roots[root_id]).as_posix()
                self.path(root_id, relative)
                out.append(
                    {
                        "path": relative,
                        "root_id": root_id,
                        "name": path.name,
                        "version": file_version(path),
                        "size_bytes": path.stat().st_size,
                    }
                )
            return out
        path = self.path(root_id, args.get("path", ""))
        if kind == "subtitle_candidates":
            from .subtitle_node import candidates

            return await candidates(self, path, args)
        if kind == "subtitle_prepare":
            from .subtitle_node import prepare

            return await prepare(self, path, args)
        if kind == "subtitle_evidence":
            from .subtitle_node import evidence

            return await evidence(self, path, args)
        if kind == "subtitle_listen":
            from .subtitle_node import listen

            return await listen(self, path, args)
        if kind == "hls_segment":
            if self._hls_cache is None:
                from .hls_cache import HLSCache

                self._hls_cache = HLSCache(self.cache_root)
            return await self._hls_cache.segment(path, args)
        if kind == "subtitle_extract":
            if args.get("version") != file_version(path):
                raise NodeError("The file changed since track selection.")
            facts = await probe_file(path)
            index = int(args["index"])
            track = next(
                (t for t in facts["subtitle_tracks"] if t["index"] == index), None
            )
            if not track or track["codec"] not in (
                "subrip",
                "ass",
                "ssa",
                "webvtt",
                "mov_text",
                "text",
            ):
                raise NodeError(
                    "This track needs image-subtitle support or a text alternative."
                )
            output = await run_media(
                "ffmpeg",
                "-v",
                "error",
                "-i",
                path,
                "-map",
                f"0:{index}",
                "-f",
                "webvtt",
                "pipe:1",
                timeout=60,
            )
            if len(output) > 2 * 1024 * 1024:
                raise NodeError("This subtitle track exceeds the supported size.")
            return {"vtt": output.decode("utf-8")}
        if kind == "probe":
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                raise NodeError("Choose a supported video file.")
            return await probe_file(path)
        if kind in ("stat", "read"):
            # Saved dialogue evidence is the one non-media document readable,
            # and only at its exact cache location.
            evidence = root_id == "cache" and re.fullmatch(
                r"subtitle-evidence/[a-f0-9]{32}/evidence\.json",
                str(args.get("path", "")),
            )
            if not path.is_file() or (
                path.suffix.lower() not in VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS
                and not evidence
            ):
                raise NodeError("This is not a supported media file.")
            version = file_version(path)
            if args.get("version") and args["version"] != version:
                raise NodeError(
                    "This media copy has changed. Refresh it before continuing."
                )
            if kind == "stat":
                return {"version": version, "size_bytes": version["size_bytes"]}
            offset, length = int(args.get("offset", 0)), int(
                args.get("length", READ_CHUNK)
            )
            if offset < 0 or not 1 <= length <= READ_CHUNK:
                raise NodeError("Invalid media byte range.")
            with path.open("rb") as source:
                source.seek(offset)
                data = source.read(length)
            if version != file_version(path):
                raise NodeError("The media changed while reading.")
            return {
                "bytes": base64.b64encode(data).decode(),
                "offset": offset,
                "version": version,
            }
        if kind == "publish":
            if root_id != "staging":
                raise NodeError(
                    "Publication sources must be in staging; existing media is preserved."
                )
            expected = args.get("version")
            if expected != file_version(path):
                raise NodeError("The source changed after verification.")
            target = self.path(
                args.get("destination_root", "library"), args["destination"], write=True
            )
            if target.suffix.lower() not in VIDEO_EXTENSIONS:
                raise NodeError("Choose a video destination.")
            target.parent.mkdir(parents=True, exist_ok=True)
            incoming = target.parent / ".sparrow-incoming"
            incoming.mkdir(exist_ok=True)
            if incoming.is_symlink():
                raise NodeError("Unsafe incoming folder.")
            temp = incoming / operation_id
            needed = max(
                0,
                expected["size_bytes"] - (temp.stat().st_size if temp.exists() else 0),
            )
            if shutil.disk_usage(target.parent).free < needed + 64 * 1024 * 1024:
                raise NodeError(
                    "There is not enough free space to safely publish this file."
                )

            # Resumable copy with end-to-end identity verification; source retained for seeding.
            def copy():
                offset = temp.stat().st_size if temp.exists() else 0
                if offset > expected["size_bytes"]:
                    raise NodeError("The incoming copy is inconsistent.")
                with path.open("rb") as source, temp.open("ab") as output:
                    source.seek(offset)
                    shutil.copyfileobj(source, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                checksum = sha256_file(path)
                if checksum != sha256_file(temp) or expected != file_version(path):
                    temp.unlink(missing_ok=True)
                    raise NodeError(
                        "The copied bytes did not match the verified source; the original is safe."
                    )
                if target.exists():
                    if sha256_file(target) != checksum:
                        raise NodeError(
                            "A different file already exists at the destination."
                        )
                else:
                    # Atomic no-clobber publication on NTFS and normal local Linux filesystems.
                    os.link(temp, target)
                temp.unlink(missing_ok=True)
                return checksum

            checksum = await asyncio.to_thread(copy)
            facts = await probe_file(target)
            return {
                "path": args["destination"],
                "root_id": args.get("destination_root", "library"),
                "sha256": checksum,
                "facts": facts,
            }
        if kind == "delete_staging":
            if (
                root_id != "staging"
                or not args.get("path")
                or len(PurePosixPath(args["path"]).parts) < 2
            ):
                raise NodeError(
                    "Only a file inside an isolated staging folder may be removed."
                )
            if path.is_dir():
                raise NodeError("Delete individual staging files, not folders.")
            path.unlink()
            return {"removed": True}
        raise NodeError("Unknown node operation.")

    async def _download(self, kind, args):
        from ..models import TorrentClientConfig
        from ..services.torrent_client import TorrentManager

        if not self.downloader:
            raise NodeError("No download app is configured on this node.")
        manager = TorrentManager(TorrentClientConfig.from_dict(self.downloader))
        if not await manager.connect():
            raise NodeError("The node cannot reach its download app.")
        if kind == "download_add":
            info_hash = args.get("info_hash", "")
            if not re.fullmatch("[a-f0-9]{40}", info_hash):
                raise NodeError("Invalid content hash.")
            path = self.path("staging", args["path"], write=True)
            existing = await manager.get_torrent_status(info_hash)
            if existing:
                if (
                    not existing.get("save_path")
                    or Path(existing["save_path"]).resolve() != path
                ):
                    raise NodeError(
                        "This torrent already exists outside this request’s staging folder. Sparrow will not take over or remove it."
                    )
                return {"hash": info_hash, "existing": True}
            from ..services.torrent_client import build_magnet

            if path == self.roots["staging"]:
                raise NodeError("Downloads need an isolated staging folder.")
            path.mkdir(parents=True, exist_ok=True)
            result = await manager.add_magnet(
                build_magnet(info_hash, args.get("name", "")), str(path)
            )
            if not result:
                raise NodeError(
                    "The download app did not confirm this transfer. Reconcile its hash before retrying."
                )
            return {"hash": result}
        info_hash = args.get("hash", "")
        if not re.fullmatch("[a-f0-9]{40}", info_hash):
            raise NodeError("Invalid content hash.")
        if kind == "download_status":
            result = await manager.get_torrent_status(info_hash)
            if result:
                result = dict(result)
                result["status"] = getattr(
                    result.get("status"), "value", result.get("status")
                )
                result.pop("save_path", None)
            return result
        current = await manager.get_torrent_status(info_hash)
        if current:
            with self.db() as db:
                owned = db.execute(
                    "SELECT payload FROM operations WHERE json_extract(payload,'$.kind')='download_add' AND json_extract(payload,'$.args.info_hash')=?",
                    (info_hash,),
                ).fetchall()
            paths = [
                self.path(
                    "staging", json.loads(r["payload"])["args"]["path"], write=True
                )
                for r in owned
            ]
            if (
                not current.get("save_path")
                or Path(current["save_path"]).resolve() not in paths
            ):
                raise NodeError(
                    "This transfer is outside Sparrow’s recorded staging folders. Its controls were left untouched."
                )
        elif kind in ("download_stop", "download_remove"):
            return {"confirmed": True, "already_absent": True}
        if kind == "download_stop":
            result = await manager.stop_torrent(info_hash)
        elif kind == "download_start":
            result = await manager.start_torrent(info_hash)
        elif kind == "download_remove":
            # Downloader never deletes files; folder jail owns any later cleanup.
            result = await manager.delete_torrent(info_hash, delete_files=False)
        else:
            raise NodeError("Unknown download operation.")
        if result is False:
            raise NodeError(
                "The download app did not confirm the requested control. It will retry when connected."
            )
        return {"confirmed": True}
        raise NodeError("Unknown download operation.")
