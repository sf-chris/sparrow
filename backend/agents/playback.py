"""Authenticated, version-bound playback and per-user resume ordering.

A playback session also has a cast link: an unguessable address for that one
session, so a TV (AirPlay, Chromecast) that fetches the video itself, without
the viewer's sign-in, can play it. The link ends with the session, and is
refused as soon as the viewer loses access.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import math
import mimetypes
import os
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
TEXT_SUBTITLES = ("subrip", "ass", "ssa", "webvtt", "mov_text", "text")
# Converted segments start 1.4 s into the MPEG-TS clock (ffmpeg's default mux
# delay), so subtitles carried in the stream map cue time 0 to that point.
STREAM_START_PTS = 126000
CAST_PREFIX = "/api/v1/cast/"
# A TV's player fetches from its own origin; the link itself is the authority.
CAST_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Expose-Headers": "Accept-Ranges, Content-Length, Content-Range",
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def link_key(data_dir):
    """The server's secret for signing cast links, created once."""
    path = Path(data_dir) / "playback-link-key"
    if not path.exists():
        spare = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
        fd = os.open(spare, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
        try:
            os.link(spare, path)  # Atomic: a concurrent start keeps one key.
        except FileExistsError:
            pass
        finally:
            spare.unlink()
    key = path.read_bytes()
    if len(key) < 32:
        raise RuntimeError(f"{path} is damaged. Delete it to issue new cast links.")
    return key


def with_stream_clock(vtt):
    """WebVTT for an HLS subtitle rendition, aligned to the converted video."""
    head, _, rest = vtt.lstrip("﻿").partition("\n")
    return (
        f"{head}\nX-TIMESTAMP-MAP=MPEGTS:{STREAM_START_PTS},LOCAL:00:00:00.000\n{rest}"
    )


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
    key = link_key(storage.data_dir)

    def cast_token(session_id):
        mac = hmac.new(key, b"cast:" + session_id.encode(), hashlib.sha256)
        return session_id + mac.hexdigest()[:32]

    @app.middleware("http")
    async def cast_links(request: Request, call_next):
        # Cast links carry their own authority (see cast_session), so the
        # sign-in boundary passes them through; this adds what TVs need.
        if not request.url.path.startswith(CAST_PREFIX):
            return await call_next(request)
        if request.method == "OPTIONS":
            return Response(
                status_code=204,
                headers={
                    **CAST_HEADERS,
                    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                    "Access-Control-Allow-Headers": "Range",
                    "Access-Control-Max-Age": "600",
                },
            )
        response = await call_next(request)
        response.headers.update(CAST_HEADERS)
        return response

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

    def open_row(session_id, user_id):
        with accounts.connect() as db:
            return db.execute(
                "SELECT * FROM playback_sessions WHERE id=? AND user_id=? AND closed=0 AND expires>?",
                (session_id, user_id, time.time()),
            ).fetchone()

    def session(request, session_id):
        user = request.state.user
        row = open_row(session_id, user["id"])
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

    def cast_session(request, token):
        """Resolve a cast link to its session, as the viewer who started it."""
        ended = HTTPException(404, "This link has ended. Start playback again.")
        if not re.fullmatch(r"[a-f0-9]{64}", token) or not hmac.compare_digest(
            token, cast_token(token[:32])
        ):
            raise ended
        with accounts.connect() as db:
            row = db.execute(
                "SELECT user_id FROM playback_sessions WHERE id=?", (token[:32],)
            ).fetchone()
        user = accounts.user(row["user_id"]) if row else None
        if not user or user["disabled"]:
            raise ended
        request.state.user = user
        try:
            row, data, asset = session(request, token[:32])
        except HTTPException as exc:
            raise ended if exc.status_code == 404 else exc

        def signed_in():
            # The link lives no longer than the sign-in that started playback:
            # signing that device out (or everywhere) ends the TV's link too.
            with accounts.connect() as db:
                return db.execute(
                    "SELECT 1 FROM login_sessions WHERE token_hash=? AND expires>?",
                    (data.get("login", ""), time.time()),
                ).fetchone()

        def allowed():
            # Checked between chunks: ending the session or the viewer's
            # access stops a TV mid-stream, as signing out does in a browser.
            viewer = accounts.user(user["id"])
            return bool(
                open_row(row["id"], user["id"])
                and signed_in()
                and accounts.can_access(viewer, asset["node_id"])
            )

        if not signed_in():
            raise ended
        return row, data, asset, allowed

    async def read_response(
        request,
        asset,
        *,
        root_id=None,
        path=None,
        version=None,
        content_type=None,
        allowed=None,
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

        def signed_in():
            from .account_api import COOKIE

            user = accounts.from_session(request.cookies.get(COOKIE))
            return bool(user and accounts.can_access(user, asset["node_id"]))

        async def chunks():
            offset = start
            while offset <= end and size:
                # Revocation applies during long streams as well as new requests.
                if not (allowed or signed_in)():
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

    def signed_in_as(request):
        from .account_api import COOKIE
        from .accounts import digest

        return digest(request.cookies.get(COOKIE) or "")

    def converts(node_id):
        return bool(
            (nodes.info(node_id) or {}).get("capabilities", {}).get("transcode")
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
        if mode == "hls" and not converts(asset["node_id"]):
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
        embedded_tracks = [
            t
            for t in facts["subtitle_tracks"]
            if t["codec"] in TEXT_SUBTITLES
            # A prepared copy of this embedded track replaces it in the list.
            and "embedded:" + str(t["index"])
            not in {p.get("source_id") for p in prepared_tracks}
        ]
        offered = [
            {
                "index": 10000 + i,
                "track": t["id"],
                "language": t["language"],
                "title": t["language"]
                + " · "
                + t["kind"]
                + (" · written by Sparrow" if t["source"] == "written" else "")
                + (" · checked" if t["sync_checked"] else ""),
            }
            for i, t in enumerate(prepared_tracks)
        ] + [
            {
                "index": t["index"],
                "language": language_code(t["language"]),
                "title": t["title"],
            }
            for t in embedded_tracks
        ]
        data = {
            "mode": mode,
            "audio_index": chosen,
            "version": facts["version"],
            "duration": facts["duration"],
            "preferences": prefs,
            "position": position,
            # What this session offered, so a cast link resolves the same
            # tracks the viewer chose from.
            "captions": offered,
            "login": signed_in_as(request),
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
        report(request, asset, "start", False)
        cast = f"{CAST_PREFIX}{cast_token(identity)}"
        by_id = {t["id"]: t for t in prepared_tracks}
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
                (
                    {
                        **{k: v for k, v in t.items() if k != "track"},
                        "id": t["track"],
                        "sync_checked": by_id[t["track"]]["sync_checked"],
                        "url": by_id[t["track"]]["url"],
                        "offset": by_id[t["track"]]["offset"],
                    }
                    if "track" in t
                    else {
                        **t,
                        "url": f'/api/v1/playback/{identity}/subtitles/{t["index"]}.vtt',
                    }
                )
                for t in offered
            ],
            # For a TV, which fetches without the viewer's sign-in. "url" plays
            # as this session does; "stream" is the converted stream that can
            # also carry a chosen subtitle (?subtitle=index) to the TV.
            "cast": {
                "url": f"{cast}/media" if direct else f"{cast}/hls/video.m3u8",
                "stream": (
                    f"{cast}/hls/index.m3u8" if converts(asset["node_id"]) else None
                ),
                "subtitles": [
                    {"index": t["index"], "url": f'{cast}/subtitles/{t["index"]}.vtt'}
                    for t in offered
                ],
            },
        }

    def segments(data):
        return math.ceil(data["duration"] / SEGMENT_SECONDS)

    def media_playlist(data):
        duration = data["duration"]
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            "#EXT-X-TARGETDURATION:6",
            "#EXT-X-MEDIA-SEQUENCE:0",
        ]
        for index in range(segments(data)):
            lines.extend(
                [
                    f"#EXTINF:{min(SEGMENT_SECONDS, duration-index*SEGMENT_SECONDS):.6f},",
                    f"{index}.ts",
                ]
            )
        lines.append("#EXT-X-ENDLIST")
        return playlist_response(lines)

    def playlist_response(lines):
        return Response(
            "\n".join(lines) + "\n",
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )

    async def serve_segment(request, session_id, data, asset, index, allowed=None):
        if not 0 <= index < segments(data):
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
                allowed=allowed,
            )
        except NodeError as exc:
            report(request, asset, "preparation", True)
            raise HTTPException(503, str(exc)) from exc

    converted = {}  # (node, root, path, version, index) -> WebVTT, newest last

    async def embedded_vtt(request, data, asset, index):
        if not any(t["index"] == index for t in asset["facts"]["subtitle_tracks"]):
            raise HTTPException(404, "Subtitle track not found.")
        key = (asset["node_id"], asset["root_id"], asset["path"], canonical(data["version"]), index)
        if key in converted:
            converted[key] = converted.pop(key)
            return converted[key]
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
            return vtt
        except NodeError as exc:
            report(request, asset, "captions", True)
            raise HTTPException(422, str(exc)) from exc

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
        return media_playlist(data)

    @router.get("/playback/{session_id}/hls/{index}.ts")
    async def segment(session_id: str, index: int, request: Request):
        _, data, asset = session(request, session_id)
        if data["mode"] != "hls":
            raise HTTPException(404, "Playback segment not found.")
        return await serve_segment(request, session_id, data, asset, index)

    @router.get("/playback/{session_id}/subtitles/{index}.vtt")
    async def subtitles(session_id: str, index: int, request: Request):
        _, data, asset = session(request, session_id)
        vtt = await embedded_vtt(request, data, asset, index)
        return Response(vtt, media_type="text/vtt")

    # ── Cast links: what a TV fetches. Each route resolves the link itself. ──

    def offered(data, index):
        caption = next((t for t in data.get("captions", []) if t["index"] == index), None)
        if not caption:
            raise HTTPException(404, "Subtitle track not found.")
        return caption

    async def caption_vtt(request, data, asset, index):
        caption = offered(data, index)
        if "track" not in caption:
            return await embedded_vtt(request, data, asset, index)
        prepared = getattr(app.state, "subtitles", None)
        if not prepared:
            raise HTTPException(404, "Subtitle track not found.")
        return await prepared.caption(request.state.user, caption["track"])

    def converted_stream(asset, data):
        if data["mode"] != "hls" and not converts(asset["node_id"]):
            raise HTTPException(
                409, "This storage can't convert video, so it can only play as it is."
            )

    @router.api_route("/cast/{token}/media", methods=["GET", "HEAD"])
    async def cast_media(token: str, request: Request):
        _, data, asset, allowed = cast_session(request, token)
        if data["mode"] != "direct":
            raise HTTPException(409, "Use the cast stream for this video.")
        return await read_response(request, asset, allowed=allowed)

    @router.api_route("/cast/{token}/hls/index.m3u8", methods=["GET", "HEAD"])
    def cast_stream(
        token: str,
        request: Request,
        subtitle: int | None = None,
        start: float | None = None,
    ):
        """The converted video, with the chosen subtitle as a rendition: a TV
        shows only what the stream itself carries."""
        _, data, asset, _ = cast_session(request, token)
        converted_stream(asset, data)
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]
        if start is not None and 0 < start < data["duration"]:
            lines.append(f"#EXT-X-START:TIME-OFFSET={start:.3f},PRECISE=YES")
        info = "BANDWIDTH=6000000,CODECS=\"avc1.640028" + (
            ",mp4a.40.2\"" if data["audio_index"] is not None else "\""
        )
        if subtitle is not None:
            caption = offered(data, subtitle)
            name = re.sub(r'["\r\n]', "", caption.get("title") or "Subtitles")
            language = language_code(caption.get("language") or "")
            lines.append(
                f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{name}",'
                + (f'LANGUAGE="{language}",' if language and language != "und" else "")
                + f'DEFAULT=YES,AUTOSELECT=YES,FORCED=NO,URI="subs/{subtitle}.m3u8"'
            )
            info += ',SUBTITLES="subs"'
        lines.extend([f"#EXT-X-STREAM-INF:{info}", "video.m3u8"])
        return playlist_response(lines)

    @router.api_route("/cast/{token}/hls/video.m3u8", methods=["GET", "HEAD"])
    def cast_video(token: str, request: Request):
        _, data, asset, _ = cast_session(request, token)
        converted_stream(asset, data)
        return media_playlist(data)

    @router.api_route("/cast/{token}/hls/{index}.ts", methods=["GET", "HEAD"])
    async def cast_segment(token: str, index: int, request: Request):
        row, data, asset, allowed = cast_session(request, token)
        converted_stream(asset, data)
        return await serve_segment(request, row["id"], data, asset, index, allowed)

    @router.api_route("/cast/{token}/hls/subs/{index}.m3u8", methods=["GET", "HEAD"])
    def cast_subtitle_playlist(token: str, index: int, request: Request):
        _, data, _, _ = cast_session(request, token)
        offered(data, index)
        duration = data["duration"]
        return playlist_response(
            [
                "#EXTM3U",
                "#EXT-X-VERSION:3",
                "#EXT-X-PLAYLIST-TYPE:VOD",
                f"#EXT-X-TARGETDURATION:{max(1, math.ceil(duration))}",
                "#EXT-X-MEDIA-SEQUENCE:0",
                f"#EXTINF:{duration:.6f},",
                f"{index}.vtt",
                "#EXT-X-ENDLIST",
            ]
        )

    @router.api_route("/cast/{token}/hls/subs/{index}.vtt", methods=["GET", "HEAD"])
    async def cast_stream_subtitle(token: str, index: int, request: Request):
        _, data, asset, _ = cast_session(request, token)
        vtt = await caption_vtt(request, data, asset, index)
        return Response(with_stream_clock(vtt), media_type="text/vtt")

    @router.api_route("/cast/{token}/subtitles/{index}.vtt", methods=["GET", "HEAD"])
    async def cast_subtitle(token: str, index: int, request: Request):
        _, data, asset, _ = cast_session(request, token)
        vtt = await caption_vtt(request, data, asset, index)
        return Response(vtt, media_type="text/vtt")

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
        # A direct session may have converted too, for a TV's stream.
        if data["mode"] == "hls" or converts(asset["node_id"]):
            try:
                await nodes.execute(
                    asset["node_id"], "hls_stop", {"session_id": session_id}, timeout=10
                )
            except NodeError:
                pass
        return {"ok": True}

    app.include_router(router)
    return {"session": session, "read_response": read_response}
