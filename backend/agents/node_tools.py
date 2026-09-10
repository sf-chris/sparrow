"""Job-bound acquisition and media tools shared by local and remote storage.

Agents see virtual root references, never a Windows path interpreted on Linux.
Publication retains seeding sources and old copies, with a durable receipt before
inventory can acknowledge a file as ready.
"""

import hashlib
import time
from pathlib import Path, PurePosixPath
from .runtime import ToolDef, ToolError
from .models import JobStatus, Event
from .media_state import audio_satisfies
from .nodes import Nodes
from .catalogue import Catalogue
from .node_executor import NodeError, canonical
from .tools import fetch_tools, media_tools, quality_rank
from ..models import Download, DownloadStatus, LibraryItem, MediaType
from ..services.torrent_client import build_magnet


def components(tb):
    if not hasattr(tb, "nodes"):
        from types import SimpleNamespace

        tb.nodes = Nodes(tb.storage, lambda: SimpleNamespace(store=tb.store))
        tb.catalogue = Catalogue(tb.storage, tb.nodes)
        with tb.accounts.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,download_id TEXT NOT NULL,node_id TEXT NOT NULL,path TEXT NOT NULL,data TEXT NOT NULL)"
            )
    return tb.nodes, tb.catalogue


def reference(tb, ctx, value, default="staging"):
    job = tb.require_authority(ctx)
    download = tb.storage.get_download(ctx.session.download_id)
    value = value or (f"staging/{download.id}" if download else default)
    if not job.node_id:
        for root, base in [
            ("staging", tb.cfg().staging_dir),
            ("library", tb.cfg().library_dir),
        ]:
            if base:
                try:
                    value = (
                        root
                        + "/"
                        + Path(value).relative_to(Path(base).resolve()).as_posix()
                    )
                    break
                except ValueError:
                    pass
    root, separator, relative = value.partition("/")
    if root not in ("staging", "library"):
        raise ToolError("Use staging/<download_id>/file or library/<relative path>.")
    if root == "staging" and (
        not download
        or not (relative == download.id or relative.startswith(download.id + "/"))
    ):
        raise ToolError("You may inspect only this download’s isolated staging folder.")
    if not relative and root == "library":
        raise ToolError("Use inventory_read to inspect this title’s library copies.")
    nodes, _ = components(tb)
    # Portable path validation happens again on the executing machine.
    return job, download, root, relative


def scoped_assets(tb, job):
    _, catalogue = components(tb)
    user = tb.accounts.user(job.user_id) if job.user_id else None
    items = [
        i
        for i in tb.storage.get_library()
        if i.tmdb_id == job.tmdb_id
        and i.media_type.value == job.media_type
        and i.metadata.get("library_id", "local") == job.library_id
    ]
    return [a for i in items for a in catalogue.assets(user, i.id)]


def suitable(job, asset):
    facts = asset["facts"]
    prefs = job.preferences.get("values", {})
    limit = prefs.get("max_file_size_gb", 0)
    policy = job.preferences.get("policy", {})
    return (
        asset["state"] == "ready"
        and audio_satisfies(facts, job.audio_pref, job.original_language)
        and quality_rank(facts["quality"]) >= quality_rank(job.min_quality)
        and quality_rank(facts["quality"])
        <= quality_rank(policy.get("max_quality", "2160p"))
        and (not limit or facts["size_bytes"] <= limit * 1e9)
    )


def acquisition_tools(tb):
    nodes, _ = components(tb)

    async def inventory(ctx, args):
        job = tb.require_authority(ctx)
        if args.get("tmdb_id", job.tmdb_id) != job.tmdb_id:
            raise ToolError("This tool is scoped to the requested title.")
        return [
            {
                "asset_id": a["id"],
                "path": "library/" + a["path"],
                "season": a.get("season"),
                "episode": a.get("episode"),
                "state": a["state"],
                "facts": a["facts"],
                "satisfies_request": suitable(job, a),
            }
            for a in scoped_assets(tb, job)
        ]

    async def add(ctx, args):
        async with tb.transfer_lock:
            job = tb.require_authority(ctx)
            node_id = job.node_id or "local"
            info_hash = str(args.get("info_hash", "")).lower()
            if len(info_hash) != 40 or any(
                c not in "0123456789abcdef" for c in info_hash
            ):
                raise ToolError("Use a valid content hash.")
            identity = (
                "dl-"
                + hashlib.sha256((node_id + ":" + info_hash).encode()).hexdigest()[:32]
            )
            existing = tb.storage.get_download(identity)
            if existing and existing.metadata.get("job_id") != job.id:
                raise ToolError(
                    "This content already belongs to another request on this storage."
                )
            if existing and existing.status not in (
                DownloadStatus.ERROR,
                DownloadStatus.QUEUED,
            ):
                return {"download_id": identity, "state": existing.status.value}
            active = [
                d
                for d in tb.storage.get_all_downloads()
                if d.id != identity
                and d.status
                in (
                    DownloadStatus.QUEUED,
                    DownloadStatus.DOWNLOADING,
                    DownloadStatus.PAUSED,
                )
            ]
            if len(active) >= max(1, tb.cfg().max_active_transfers):
                raise ToolError(
                    "The transfer limit is reached. Wait for an existing download to finish."
                )
            if not existing:
                existing = Download(
                    id=identity,
                    name=args.get("name") or info_hash,
                    magnet_url=build_magnet(info_hash, args.get("name", "")),
                    media_type=MediaType(job.media_type),
                    status=DownloadStatus.QUEUED,
                    torrent_hash=info_hash,
                    staging_path=f"staging/{identity}",
                    tmdb_id=job.tmdb_id,
                    metadata={
                        "job_id": job.id,
                        "session_id": ctx.session.id,
                        "job_revision": job.revision,
                        "agent_managed": True,
                        "node_id": node_id,
                        "desired_control": "",
                    },
                )
                await tb.storage.add_download(existing)
            if existing.status == DownloadStatus.ERROR:
                existing.metadata.update(
                    {
                        "landed_emitted": False,
                        "job_revision": job.revision,
                        "desired_control": "",
                    }
                )
                await tb.storage.update_download(
                    identity, status=DownloadStatus.QUEUED, metadata=existing.metadata
                )
            try:
                result = await nodes.execute(
                    node_id,
                    "download_add",
                    {"info_hash": info_hash, "name": existing.name, "path": identity},
                    job=job,
                    operation_id=f"add-{identity}-{job.revision}",
                    timeout=30,
                )
            except NodeError as exc:
                await tb.storage.update_download(
                    identity,
                    error_message="Waiting for storage to confirm the transfer.",
                )
                raise ToolError(
                    str(exc) + " The reserved transfer will reconcile before a retry."
                ) from exc
            tb.require_authority(ctx)
            await tb.storage.update_download(
                identity, status=DownloadStatus.DOWNLOADING, error_message=""
            )
            await tb.broadcast(
                {
                    "type": "download_added",
                    "data": tb.storage.get_download(identity).to_dict(),
                }
            )
            return {
                "download_id": identity,
                "staging": f"staging/{identity}",
                "recorded": True,
            }

    async def status(ctx, args):
        job = tb.require_authority(ctx)
        out = []
        for dl in tb.storage.get_all_downloads():
            if dl.metadata.get("job_id") != job.id:
                continue
            result = await nodes.execute(
                job.node_id or "local",
                "download_status",
                {"hash": dl.torrent_hash},
                timeout=15,
            )
            out.append(
                {
                    "download_id": dl.id,
                    "status": result,
                    "last_recorded": dl.status.value,
                }
            )
        return out

    async def remove(ctx, args):
        job = tb.require_authority(ctx)
        dl = tb.storage.get_download(args.get("download_id", ""))
        if not dl or dl.metadata.get("job_id") != job.id:
            raise ToolError("This transfer does not belong to the request.")
        dl.metadata["desired_control"] = "remove"
        await tb.storage.update_download(dl.id, metadata=dl.metadata)
        try:
            await nodes.execute(
                job.node_id or "local",
                "download_remove",
                {"hash": dl.torrent_hash},
                job=job,
                operation_id=f"remove-{dl.id}-{job.revision}",
                timeout=20,
            )
        except NodeError as exc:
            raise ToolError(
                "Removal is pending storage reconnection. " + str(exc)
            ) from exc
        dl.metadata["desired_control"] = ""
        await tb.storage.update_download(
            dl.id,
            status=DownloadStatus.ERROR,
            error_message=args.get("reason")
            or "Removed from download app; files retained.",
            metadata=dl.metadata,
        )
        return "Transfer removed. Existing files and staging originals are preserved."

    async def close(ctx, args):
        job = tb.require_authority(ctx)
        outcome = args.get("outcome")
        if outcome not in ("complete", "abandoned"):
            raise ToolError("Choose complete or abandoned.")
        if outcome == "complete":
            ready = [a for a in scoped_assets(tb, job) if suitable(job, a)]
            needed = (
                [
                    (int(s), int(e))
                    for s, eps in job.wanted_episodes.items()
                    for e in eps
                ]
                if job.media_type == "tv"
                else [(None, None)]
            )
            for season, episode in needed:
                matches = [
                    a
                    for a in ready
                    if (a.get("season"), a.get("episode")) == (season, episode)
                ]
                if not matches:
                    raise ToolError(
                        "Completion refused: verified available copies do not satisfy every requested item and preference."
                    )
                a = matches[0]
                await nodes.execute(
                    a["node_id"],
                    "stat",
                    {
                        "root_id": a["root_id"],
                        "path": a["path"],
                        "version": a["facts"]["version"],
                    },
                    job=job,
                    timeout=20,
                )
                tb.require_authority(ctx)
                if job.preferences.get("values", {}).get("require_subtitles"):
                    subtitles = getattr(tb, "subtitles", None)
                    user = tb.accounts.user(job.user_id)
                    if not subtitles or not subtitles.satisfies(
                        user, a["id"], job.preferences["values"]
                    ):
                        raise ToolError(
                            "Media is playable, but required subtitles have not passed preparation and review. Wait for subtitles_ready or ask the user to repair subtitles; do not mark the request complete."
                        )
        job.status = (
            JobStatus.COMPLETE if outcome == "complete" else JobStatus.ABANDONED
        )
        job.state_line = args.get("note") or (
            "Ready to watch." if outcome == "complete" else "Needs your attention."
        )
        job.closed_at = time.time()
        job.next_wake_at = 0
        tb.store.save_job(job)
        ctx.close = True
        ctx.close_reason = job.state_line
        await tb.broadcast({"type": "job_update", "data": job.to_dict()})
        return job.state_line

    handlers = {
        "inventory_read": inventory,
        "client_add": add,
        "client_status": status,
        "client_remove": remove,
        "job_close": close,
    }
    return [
        ToolDef(t.name, t.description, t.input_schema, handlers.get(t.name, t.handler))
        for t in fetch_tools(tb)
    ]


def storage_tools(tb):
    nodes, catalogue = components(tb)

    async def operation(ctx, kind, value, **extra):
        job, download, root, path = reference(tb, ctx, value)
        if root == "library" and kind not in ("publish",):
            owned = any(a["path"] == path for a in scoped_assets(tb, job))
            with tb.accounts.connect() as db:
                placed = db.execute(
                    "SELECT 1 FROM publications WHERE job_id=? AND path=?",
                    (job.id, path),
                ).fetchone()
            if not owned and not placed:
                raise ToolError(
                    "This library file does not belong to the requested title."
                )
        try:
            result = await nodes.execute(
                job.node_id or "local",
                kind,
                {"root_id": root, "path": path, **extra},
                job=job,
                timeout=120,
            )
        except NodeError as exc:
            raise ToolError(str(exc)) from exc
        tb.require_authority(ctx)
        return result

    async def listing(ctx, args):
        rows = await operation(ctx, "list", args.get("path"))
        return [{**r, "path": r["root_id"] + "/" + r["path"]} for r in rows]

    async def probe(ctx, args):
        facts = await operation(ctx, "probe", args["path"])
        return {
            **facts,
            "path": args["path"],
            "duration_minutes": facts["duration"] / 60,
        }

    async def move(ctx, args):
        job, dl, root, source = reference(tb, ctx, args["src"])
        if root != "staging":
            raise ToolError(
                "Publication requires a file in this download’s staging folder."
            )
        _, _, destination_root, destination = reference(tb, ctx, args["dst"])
        if destination_root != "library":
            raise ToolError(
                "Publish into library/<relative path>. Staging originals stay in place."
            )
        facts = await operation(ctx, "probe", args["src"])
        identity = hashlib.sha256(
            canonical([job.id, dl.id, source, destination, facts["version"]]).encode()
        ).hexdigest()
        try:
            result = await nodes.execute(
                job.node_id or "local",
                "publish",
                {
                    "root_id": "staging",
                    "path": source,
                    "version": facts["version"],
                    "destination": destination,
                },
                job=job,
                operation_id="pub-" + identity[:48],
                timeout=300,
            )
        except NodeError as exc:
            raise ToolError(str(exc)) from exc
        with tb.accounts.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO publications VALUES (?,?,?,?,?,?)",
                (
                    identity,
                    job.id,
                    dl.id,
                    job.node_id or "local",
                    destination,
                    canonical(result),
                ),
            )
        tb.require_authority(ctx)
        return {
            "path": "library/" + destination,
            "facts": result["facts"],
            "source_preserved": True,
        }

    async def delete(ctx, args):
        job, dl, root, path = reference(tb, ctx, args["path"])
        with tb.accounts.connect() as db:
            published = db.execute(
                "SELECT 1 FROM publications WHERE download_id=?", (dl.id,)
            ).fetchone()
        if published:
            raise ToolError(
                "Published downloads retain their staging originals for seeding and recovery."
            )
        return await operation(ctx, "delete_staging", args["path"])

    async def upgrade(ctx, args):
        old = await operation(ctx, "probe", args["old_path"])
        new = await operation(ctx, "probe", args["new_path"])
        if abs(old["duration"] - new["duration"]) > 120 or quality_rank(
            new["quality"]
        ) <= quality_rank(old["quality"]):
            raise ToolError(
                "The new copy must have matching duration and higher measured picture quality."
            )
        name = args.get("new_name") or PurePosixPath(args["new_path"]).name
        destination = str(PurePosixPath(args["old_path"]).parent / name)
        if destination == args["old_path"]:
            destination = str(
                PurePosixPath(destination).with_stem(
                    PurePosixPath(destination).stem + " - " + new["quality"]
                )
            )
        result = await move(ctx, {"src": args["new_path"], "dst": destination})
        return {
            **result,
            "previous_copy_preserved": True,
            "next": "Record the new path with inventory_write.",
        }

    async def record(ctx, args):
        job = tb.require_authority(ctx)
        if (
            args.get("tmdb_id") != job.tmdb_id
            or args.get("media_type", job.media_type) != job.media_type
        ):
            raise ToolError("Inventory must match the requested title.")
        season, episode = (
            (args.get("season"), args.get("episode"))
            if job.media_type == "tv"
            else (None, None)
        )
        if job.media_type == "tv" and episode not in job.wanted_episodes.get(
            str(season), []
        ):
            raise ToolError("This episode is outside the exact request.")
        _, dl, root, path = reference(tb, ctx, args["path"])
        if root != "library":
            raise ToolError("Publish the media into the library before recording it.")
        with tb.accounts.connect() as db:
            receipt = db.execute(
                "SELECT data FROM publications WHERE job_id=? AND download_id=? AND node_id=? AND path=?",
                (job.id, dl.id, job.node_id or "local", path),
            ).fetchone()
        if not receipt:
            raise ToolError(
                "No publication receipt proves that this file came from this download."
            )
        facts = await operation(ctx, "probe", args["path"])
        import json

        if json.loads(receipt["data"])["facts"]["version"] != facts["version"]:
            raise ToolError("The publication changed before it was recorded.")
        details = await tb.tmdb_get(f"/{job.media_type}/{job.tmdb_id}")
        expected = details.get("runtime", 0)
        if job.media_type == "tv":
            data = await tb.tmdb_get(f"/tv/{job.tmdb_id}/season/{season}")
            ep = next(
                (
                    e
                    for e in data.get("episodes", [])
                    if e.get("episode_number") == episode
                ),
                {},
            )
            expected = ep.get("runtime") or next(
                iter(details.get("episode_run_time") or []), 0
            )
        seconds = float(expected or 0) * 60
        if not seconds or abs(facts["duration"] - seconds) > min(
            120, max(5, seconds * 0.1)
        ):
            raise ToolError(
                "Measured duration does not match the catalogue runtime. Resolve the identity before recording this copy."
            )
        candidate = {"state": "ready", "facts": facts}
        if not suitable(job, candidate):
            raise ToolError(
                "The measured audio, picture quality or size does not meet this request."
            )
        tb.require_authority(ctx)
        catalogue.cache_title(job.media_type, job.tmdb_id, details)
        item_id = hashlib.sha256(
            f"{job.library_id}:{job.media_type}:{job.tmdb_id}".encode()
        ).hexdigest()[:24]
        item = tb.storage.get_library_item(item_id) or LibraryItem(
            id=item_id,
            title=job.title,
            tmdb_id=job.tmdb_id,
            media_type=MediaType(job.media_type),
            path="",
            overview=details.get("overview", ""),
            poster_path=details.get("poster_path") or "",
            metadata={"library_id": job.library_id},
        )
        asset = catalogue.save_asset(
            item.id,
            job.node_id or "local",
            "library",
            path,
            facts,
            season=season,
            episode=episode,
        )
        media = {
            "verified": True,
            "asset_id": asset,
            "source_download_id": dl.id,
            "quality": facts["quality"],
            "audio_languages": facts["audio_languages"],
            "audio_tracks": facts["audio_tracks"],
            "duration_minutes": facts["duration"] / 60,
            "file_version": facts["version"],
            "size_bytes": facts["size_bytes"],
            "added_at": time.time(),
        }
        if job.node_id:
            media.update({"node_id": job.node_id, "path": path, "available": True})
        else:
            media["path"] = str(nodes.local().path("library", path))
        if job.media_type == "movie":
            item.path = media["path"]
            item.metadata.update(media)
            item.size_bytes = facts["size_bytes"]
        else:
            item.set_episode_file(season, episode, media)
        await tb.storage.add_library_item(item)
        await tb.broadcast({"type": "library_update", "data": item.to_dict()})
        subtitles = getattr(tb, "subtitles", None)
        if (
            subtitles
            and job.preferences.get("values", {}).get("subtitle_mode", "off") != "off"
        ):
            user = tb.accounts.user(job.user_id)
            # Optional preparation belongs to the person even after Fetch closes.
            await subtitles.enqueue(
                user,
                asset,
                job=job if job.preferences["values"].get("require_subtitles") else None,
            )
        return {"asset_id": asset, "verified": True, "path": args["path"]}

    async def done(ctx, args):
        job = tb.require_authority(ctx)
        dl = tb.storage.get_download(ctx.session.download_id)
        found = False
        for asset in scoped_assets(tb, job):
            if asset["state"] != "ready":
                continue
            item = tb.storage.get_library_item(asset["item_id"])
            records = (
                [item.metadata]
                if job.media_type == "movie"
                else [r for s in item.episodes.values() for r in s.values()]
            )
            if any(r.get("source_download_id") == dl.id for r in records):
                found = True
        if not found:
            raise ToolError(
                "Record at least one verified publication before completing this media session. Report rejected files to Fetch."
            )
        await tb.storage.update_download(
            dl.id, status=DownloadStatus.ORGANIZED, completed_at=time.time()
        )
        await tb.emit(
            Event(
                kind="media_report",
                job_id=job.id,
                download_id=dl.id,
                payload={
                    "description": args.get(
                        "summary", "Verified media is in the library."
                    )
                },
            )
        )
        ctx.close = True
        ctx.close_reason = args.get("summary", "Media processed.")
        return "Session completed; staging originals and previous library copies are preserved."

    handlers = {
        "fs_list": listing,
        "fs_probe": probe,
        "fs_move": move,
        "fs_delete": delete,
        "upgrade_swap": upgrade,
        "inventory_write": record,
        "session_done": done,
    }
    out = []
    for tool in media_tools(tb):
        schema = tool.input_schema
        if tool.name == "inventory_write":
            schema = {**schema, "required": ["tmdb_id", "path"]}
        description = tool.description
        if tool.name == "fs_move":
            description = "Publish a verified copy from staging/<download_id>/file to library/<relative path>. Retains the source and refuses overwrite."
        if tool.name == "upgrade_swap":
            description = "Publish a measured higher-quality copy alongside the previous copy. Both original files survive."
        out.append(
            ToolDef(
                tool.name, description, schema, handlers.get(tool.name, tool.handler)
            )
        )
    out.append(next(t for t in acquisition_tools(tb) if t.name == "inventory_read"))
    return out
