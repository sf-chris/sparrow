"""Authenticated, version-bound playback and per-user resume ordering."""

import asyncio
import base64
import json
import math
import mimetypes
import re
import secrets
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .media_state import language_code
from .node_executor import NodeError, READ_CHUNK, canonical

SEGMENT_SECONDS = 6


def byte_range(header, size):
    if not header:
        return 0, max(0, size - 1), False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or (not match[1] and not match[2]) or size == 0:
        raise ValueError("Invalid byte range.")
    if not match[1]:
        suffix = int(match[2])
        if suffix <= 0:
            raise ValueError("Invalid suffix range.")
        return max(0, size - suffix), size - 1, True
    start, end = int(match[1]), min(int(match[2]) if match[2] else size - 1, size - 1)
    if start >= size or end < start:
        raise ValueError("Range is outside the media file.")
    return start, end, True


def install_playback(app, storage, accounts, nodes, catalogue):
    with accounts.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS playback_sessions(id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                asset_id TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                closed INTEGER NOT NULL DEFAULT 0);
        """)
    router = APIRouter(prefix="/api/v1")

    def report(request, asset, phase, failed):
        captions = phase == "captions"
        code = ("captions" if captions else "playback") + (
            "_failed" if failed else "_recovered"
        )
        storage.operations.media(
            code,
            request.state.user,
            asset,
            storage,
            observed=(phase, "failed" if failed else "ready"),
        )

    def session(request, session_id):
        user = request.state.user
        with accounts.connect() as db:
            row = db.execute(
                "SELECT * FROM playback_sessions WHERE id=? AND user_id=? AND closed=0 AND expires>?",
                (session_id, user["id"], time.time()),
            ).fetchone()
        if not row:
            raise HTTPException(
                404, "This playback session has ended. Start playback again."
            )
        asset = catalogue.asset(user, row["asset_id"])
        if not asset:
            raise HTTPException(
                404, "This media is no longer available to your account."
            )
        data = json.loads(row["data"])
        if data["version"] != asset["facts"]["version"]:
            report(request, asset, "start", True)
            raise HTTPException(
                409,
                "This media copy changed. Start playback again to use the new copy.",
            )
        return dict(row), data, asset

    async def read_response(
        request, asset, *, root_id=None, path=None, version=None, content_type=None
    ):
        root_id, path = root_id or asset["root_id"], path or asset["path"]
        version = version or asset["facts"]["version"]
        try:
            await nodes.execute(
                asset["node_id"],
                "stat",
                {"root_id": root_id, "path": path, "version": version},
                timeout=20,
            )
        except NodeError as exc:
            report(request, asset, "delivery", True)
            raise HTTPException(409, str(exc)) from exc
        size = version["size_bytes"]
        try:
            start, end, partial = byte_range(request.headers.get("range"), size)
        except ValueError:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
            )
        media_type = (
            content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
        )
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1 if size else 0),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if request.method == "HEAD":
            return Response(
                status_code=206 if partial else 200,
                headers=headers,
                media_type=media_type,
            )

        async def chunks():
            offset = start
            while offset <= end and size:
                # Revocation applies during long streams as well as new requests.
                from .account_api import COOKIE

                user = accounts.from_session(request.cookies.get(COOKIE))
                if not user or not accounts.can_access(user, asset["node_id"]):
                    return
                try:
                    result = await nodes.execute(
                        asset["node_id"],
                        "read",
                        {
                            "root_id": root_id,
                            "path": path,
                            "version": version,
                            "offset": offset,
                            "length": min(READ_CHUNK, end - offset + 1),
                        },
                        timeout=30,
                    )
                    block = base64.b64decode(result["bytes"], validate=True)
                    if not block or len(block) > end - offset + 1:
                        raise NodeError("Storage returned an incomplete media range.")
                except (NodeError, ValueError, OSError):
                    report(request, asset, "delivery", True)
                    raise
                if offset == start:
                    report(request, asset, "delivery", False)
                offset += len(block)
                yield block

        return StreamingResponse(
            chunks(),
            status_code=206 if partial else 200,
            headers=headers,
            media_type=media_type,
        )

    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class Start(Input):
        asset_id: str
        audio_index: int | None = Field(default=None, ge=0)
        position: float | None = Field(default=None, ge=0, allow_inf_nan=False)
        force_transcode: bool = False
        supported_video: list[Literal["h264", "hevc", "vp9", "av1"]] = Field(
            default_factory=lambda: ["h264"]
        )

    class Progress(Input):
        position: float = Field(ge=0, allow_inf_nan=False)
        sequence: int = Field(ge=0)
        ended: bool = False

    class Watched(Input):
        watched: bool

    @router.get("/assets/{asset_id}")
    def asset_detail(asset_id: str, request: Request):
        asset = catalogue.asset(request.state.user, asset_id)
        if not asset:
            raise HTTPException(404, "This media is not available to your account.")
        item = catalogue.item(request.state.user, asset["item_id"])
        return {
            "asset": asset,
            "title": item.title if item else "Your media",
            "preferences": accounts.resolve(request.state.user["id"]),
        }

    @router.post("/playback")
    async def start(body: Start, request: Request):
        user = request.state.user
        asset = catalogue.asset(user, body.asset_id)
        if not asset:
            raise HTTPException(404, "This media is not available to your account.")
        if asset["state"] != "ready":
            report(request, asset, "start", True)
            raise HTTPException(
                409,
                "This copy is not available. Reconnect its storage or verify the changed file.",
            )
        try:
            await nodes.execute(
                asset["node_id"],
                "stat",
                {
                    "root_id": asset["root_id"],
                    "path": asset["path"],
                    "version": asset["facts"]["version"],
                },
                timeout=20,
            )
        except NodeError as exc:
            report(request, asset, "start", True)
            raise HTTPException(409, str(exc)) from exc
        facts = asset["facts"]
        audio = facts["audio_tracks"]
        prefs = accounts.resolve(user["id"])
        if body.audio_index is not None and not any(
            t["index"] == body.audio_index for t in audio
        ):
            raise HTTPException(422, "Choose an audio track from this media copy.")
        wanted = prefs["values"]["audio_pref"]
        if wanted == "original":
            item = catalogue.item(user, asset["item_id"])
            details = (
                catalogue.cached_title(item.media_type.value, item.tmdb_id)
                if item and item.tmdb_id
                else {}
            )
            wanted = (details or {}).get("original_language", "")
        chosen = (
            body.audio_index
            if body.audio_index is not None
            else next(
                (
                    t["index"]
                    for t in audio
                    if language_code(t["language"]) == language_code(wanted)
                ),
                next(
                    (t["index"] for t in audio if t.get("default")),
                    audio[0]["index"] if audio else None,
                ),
            )
        )
        chosen_track = next((t for t in audio if t["index"] == chosen), None)
        suffix = Path(asset["path"]).suffix.lower()
        compatible_container = suffix in (".mp4", ".m4v") or (
            suffix == ".webm" and facts["video_codec"] in ("vp9", "av1")
        )
        direct = bool(
            not body.force_transcode
            and compatible_container
            and facts["video_codec"] in body.supported_video
            and (
                not audio
                or (
                    chosen == audio[0]["index"]
                    and chosen_track["codec"] in ("aac", "mp3", "opus", "vorbis")
                )
            )
        )
        mode = "direct" if direct else "hls"
        node = nodes.info(asset["node_id"])
        if mode == "hls" and not (node or {}).get("capabilities", {}).get("transcode"):
            report(request, asset, "start", True)
            raise HTTPException(
                422,
                "This browser needs a compatible playback copy. Enable the packaged media tools on this storage node.",
            )
        identity = secrets.token_hex(16)
        previous = asset.get("watch") or {}
        requested_position = (
            body.position
            if body.position is not None
            else (0 if previous.get("watched") else previous.get("position", 0))
        )
        position = min(requested_position, max(0, facts["duration"] - 0.1))
        data = {
            "mode": mode,
            "audio_index": chosen,
            "version": facts["version"],
            "duration": facts["duration"],
            "preferences": prefs,
            "position": position,
        }
        now = time.time()
        with accounts.connect() as db:
            db.execute(
                "INSERT INTO playback_sessions VALUES (?, ?, ?, ?, ?, ?, 0)",
                (
                    identity,
                    user["id"],
                    asset["id"],
                    canonical(data),
                    now,
                    now + 24 * 3600,
                ),
            )
            # The newest session owns progress. A late update from a previous
            # device cannot overwrite an intentional rewind or a newer session.
            db.execute(
                "INSERT INTO watch_state VALUES (?, ?, ?, ?, 0, ?, ?, -1) ON CONFLICT(user_id,asset_id) "
                "DO UPDATE SET session_id=excluded.session_id,sequence=-1",
                (user["id"], asset["id"], position, facts["duration"], now, identity),
            )
        prepared = getattr(app.state, "subtitles", None)
        prepared_tracks = (
            [
                t
                for t in prepared.tracks(user, asset["id"])
                if t["state"] == "ready" and t["audio_index"] == chosen
            ]
            if prepared
            else []
        )
        report(request, asset, "start", False)
        return {
            "id": identity,
            "mode": mode,
            "position": position,
            "duration": facts["duration"],
            "audio_index": chosen,
            "url": (
                f"/api/v1/playback/{identity}/media"
                if direct
                else f"/api/v1/playback/{identity}/hls/index.m3u8"
            ),
            "subtitles": [
                {
                    "index": 10000 + i,
                    "id": t["id"],
                    "language": t["language"],
                    "title": t["language"]
                    + " · "
                    + t["kind"]
                    + (" · written by Sparrow" if t["source"] == "written" else "")
                    + (" · checked" if t["sync_checked"] else ""),
                    "sync_checked": t["sync_checked"],
                    "url": t["url"],
                    "offset": t["offset"],
                }
                for i, t in enumerate(prepared_tracks)
            ]
            + [
                {
                    "index": t["index"],
                    "language": language_code(t["language"]),
                    "title": t["title"],
                    "url": f'/api/v1/playback/{identity}/subtitles/{t["index"]}.vtt',
                }
                for t in facts["subtitle_tracks"]
                if t["codec"] in ("subrip", "ass", "ssa", "webvtt", "mov_text", "text")
                # A prepared copy of this embedded track replaces it in the list.
                and "embedded:" + str(t["index"])
                not in {p.get("source_id") for p in prepared_tracks}
            ],
        }

    @router.api_route("/playback/{session_id}/media", methods=["GET", "HEAD"])
    async def media(session_id: str, request: Request):
        row, data, asset = session(request, session_id)
        if data["mode"] != "direct":
            raise HTTPException(409, "Use the prepared playback stream.")
        return await read_response(request, asset)

    @router.get("/playback/{session_id}/hls/index.m3u8")
    def playlist(session_id: str, request: Request):
        _, data, asset = session(request, session_id)
        if data["mode"] != "hls":
            raise HTTPException(409, "This session uses direct playback.")
        duration = data["duration"]
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            "#EXT-X-TARGETDURATION:6",
            "#EXT-X-MEDIA-SEQUENCE:0",
        ]
        for index in range(math.ceil(duration / SEGMENT_SECONDS)):
            lines.extend(
                [
                    f"#EXTINF:{min(SEGMENT_SECONDS, duration-index*SEGMENT_SECONDS):.6f},",
                    f"{index}.ts",
                ]
            )
        lines.append("#EXT-X-ENDLIST")
        return Response(
            "\n".join(lines) + "\n",
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/playback/{session_id}/hls/{index}.ts")
    async def segment(session_id: str, index: int, request: Request):
        _, data, asset = session(request, session_id)
        if data["mode"] != "hls" or not 0 <= index < math.ceil(
            data["duration"] / SEGMENT_SECONDS
        ):
            raise HTTPException(404, "Playback segment not found.")
        try:
            prepared = await nodes.execute(
                asset["node_id"],
                "hls_segment",
                {
                    "root_id": asset["root_id"],
                    "path": asset["path"],
                    "version": data["version"],
                    "session_id": session_id,
                    "index": index,
                    "audio_index": data["audio_index"],
                },
                timeout=90,
            )
            report(request, asset, "preparation", False)
            return await read_response(
                request,
                asset,
                root_id="cache",
                path=prepared["path"],
                version=prepared["version"],
                content_type="video/mp2t",
            )
        except NodeError as exc:
            report(request, asset, "preparation", True)
            raise HTTPException(503, str(exc)) from exc

    converted = {}  # (node, root, path, version, index) -> WebVTT, newest last

    @router.get("/playback/{session_id}/subtitles/{index}.vtt")
    async def subtitles(session_id: str, index: int, request: Request):
        _, data, asset = session(request, session_id)
        if not any(t["index"] == index for t in asset["facts"]["subtitle_tracks"]):
            raise HTTPException(404, "Subtitle track not found.")
        key = (asset["node_id"], asset["root_id"], asset["path"], canonical(data["version"]), index)
        if key in converted:
            converted[key] = converted.pop(key)
            return Response(converted[key], media_type="text/vtt")
        try:
            result = await nodes.execute(
                asset["node_id"],
                "subtitle_extract",
                {
                    "root_id": asset["root_id"],
                    "path": asset["path"],
                    "version": data["version"],
                    "index": index,
                },
                timeout=60,
            )
            report(request, asset, "captions", False)
            if "vtt" in result:
                vtt = result["vtt"]
            else:
                from .subtitle_worker import cues_from_text, render

                try:
                    # A large typeset track takes seconds to parse: off the
                    # event loop, and once per track version.
                    vtt = await asyncio.to_thread(
                        lambda: render(cues_from_text(result["text"], result["format"]), vtt=True)
                    )
                except ValueError as exc:
                    raise NodeError(str(exc)) from exc
            converted[key] = vtt
            while len(converted) > 16:
                converted.pop(next(iter(converted)))
            return Response(vtt, media_type="text/vtt")
        except NodeError as exc:
            report(request, asset, "captions", True)
            raise HTTPException(422, str(exc)) from exc

    @router.put("/playback/{session_id}/progress")
    def progress(session_id: str, body: Progress, request: Request):
        _, data, asset = session(request, session_id)
        position = min(body.position, data["duration"])
        watched = bool(
            (body.ended and position >= data["duration"] - 0.5)
            or (data["duration"] > 0 and position / data["duration"] >= 0.95)
        )
        with accounts.connect() as db:
            cursor = db.execute(
                "UPDATE watch_state SET position=?,duration=?,watched=?,updated=?,sequence=? "
                "WHERE user_id=? AND asset_id=? AND session_id=? AND sequence<?",
                (
                    position,
                    data["duration"],
                    int(watched),
                    time.time(),
                    body.sequence,
                    request.state.user["id"],
                    asset["id"],
                    session_id,
                    body.sequence,
                ),
            )
        return {"saved": cursor.rowcount == 1}

    @router.post("/assets/{asset_id}/watched")
    def watched(asset_id: str, body: Watched, request: Request):
        asset = catalogue.asset(request.state.user, asset_id)
        if not asset:
            raise HTTPException(404, "Media not found.")
        duration = asset["facts"]["duration"]
        with accounts.connect() as db:
            db.execute(
                "INSERT INTO watch_state VALUES (?, ?, ?, ?, ?, ?, ?, 0) ON CONFLICT(user_id,asset_id) DO UPDATE SET "
                "position=excluded.position,watched=excluded.watched,updated=excluded.updated,session_id=excluded.session_id,sequence=0",
                (
                    request.state.user["id"],
                    asset_id,
                    duration if body.watched else 0,
                    duration,
                    int(body.watched),
                    time.time(),
                    "manual-" + secrets.token_hex(8),
                ),
            )
        return {"ok": True}

    @router.delete("/playback/{session_id}")
    async def close(session_id: str, request: Request):
        _, data, asset = session(request, session_id)
        with accounts.connect() as db:
            db.execute(
                "UPDATE playback_sessions SET closed=1 WHERE id=?", (session_id,)
            )
        if data["mode"] == "hls":
            try:
                await nodes.execute(
                    asset["node_id"], "hls_stop", {"session_id": session_id}, timeout=10
                )
            except NodeError:
                pass
        return {"ok": True}

    app.include_router(router)
    return {"session": session, "read_response": read_response}
