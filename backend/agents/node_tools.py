"""Job-bound acquisition and media tools shared by local and remote storage.

Agents see virtual root references, never a Windows path interpreted on Linux.
Publication retains seeding sources and old copies, with a durable receipt before
inventory can acknowledge a file as ready.
"""

import asyncio
import hashlib
import re
import logging
import time
from pathlib import Path, PurePosixPath
from .runtime import ToolDef, ToolError
from .models import JobStatus, Event
from .media_state import audio_satisfies
from .nodes import Nodes
from .catalogue import Catalogue

logger = logging.getLogger(__name__)
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


def unsuitable(job, facts):
    """Which part of the request a measured copy fails, in plain words."""
    prefs = job.preferences.get("values", {})
    limit = prefs.get("max_file_size_gb", 0)
    policy = job.preferences.get("policy", {})
    reasons = []
    if not audio_satisfies(facts, job.audio_pref, job.original_language):
        heard = ", ".join(facts.get("audio_languages") or []) or "untagged"
        reasons.append(f"its audio is {heard}, and this request wants {job.audio_pref} audio"
                       + (f" ({job.original_language})" if job.audio_pref == "original" and job.original_language else ""))
    if quality_rank(facts["quality"]) < quality_rank(job.min_quality):
        reasons.append(f"its picture is {facts['quality']}, below the {job.min_quality} minimum")
    if quality_rank(facts["quality"]) > quality_rank(policy.get("max_quality", "2160p")):
        reasons.append(f"its picture is {facts['quality']}, above the household's {policy.get('max_quality')} limit")
    if limit and facts["size_bytes"] > limit * 1e9:
        reasons.append(f"it is {facts['size_bytes'] / 1e9:.1f} GB, over the {limit} GB limit")
    return reasons


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
                tb.wait_for_slot(job.id)
                raise ToolError(
                    "The transfer limit is reached. You're queued and will be woken as soon "
                    "as a slot opens: hibernate without a timer."
                )
            wanted_files = [str(f) for f in (args.get("files") or []) if str(f).strip()][:200]
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
                        **({"wanted_files": wanted_files} if wanted_files else {}),
                        **({"wanted_titles": [str(t) for t in args.get("titles") or [job.title]][:3]}),
                    },
                )
                await tb.storage.add_download(existing)
            if existing.status == DownloadStatus.ERROR:
                # Adding a removed transfer again is a new attempt: a new
                # operation (the old receipt says "added"), a fresh file choice.
                existing.metadata.update(
                    {
                        "landed_emitted": False,
                        "job_revision": job.revision,
                        "desired_control": "",
                        "attempt": int(existing.metadata.get("attempt") or 0) + 1,
                    }
                )
                existing.metadata.pop("selection", None)
                if wanted_files:
                    existing.metadata["wanted_files"] = wanted_files
                await tb.storage.update_download(
                    identity, status=DownloadStatus.QUEUED, metadata=existing.metadata,
                    progress=0.0, error_message="",
                )
            try:
                result = await nodes.execute(
                    node_id,
                    "download_add",
                    {"info_hash": info_hash, "name": existing.name, "path": identity},
                    job=job,
                    operation_id=f"add-{identity}-{job.revision}"
                    + (f"-{existing.metadata['attempt']}" if existing.metadata.get("attempt") else ""),
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
            tb.slot_taken(job.id)
            if tb.selection_for(tb.storage.get_download(identity) or existing):
                asyncio.create_task(tb.select_soon(identity))
            ctx.facts.update(download_id=identity, name=existing.name[:200], files=len(wanted_files),
                             attempt=existing.metadata.get("attempt", 0))
            return {
                "download_id": identity,
                "staging": f"staging/{identity}",
                "recorded": True,
                **({"note": "Only the chosen files will download once the torrent's file list arrives."} if wanted_files else {}),
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
        # Each re-added attempt is removed by its own operation (a reused id
        # would answer from the first removal's receipt and do nothing).
        attempt = f"-{dl.metadata['attempt']}" if dl.metadata.get("attempt") else ""
        ctx.facts.update(download_id=dl.id, name=dl.name[:200], progress=round(float(dl.progress or 0), 3))
        try:
            await nodes.execute(
                job.node_id or "local",
                "download_remove",
                {"hash": dl.torrent_hash},
                job=job,
                operation_id=f"remove-{dl.id}-{job.revision}{attempt}",
                timeout=20,
            )
        except NodeError as exc:
            raise ToolError(
                "Removal is pending storage reconnection. " + str(exc)
            ) from exc
        dl.metadata["desired_control"] = ""
        with tb.accounts.connect() as db:
            published = db.execute("SELECT 1 FROM publications WHERE download_id=?", (dl.id,)).fetchone()
        discarded = False
        if not published:
            # Nothing reached the library: its partial files are rubbish.
            try:
                result = await nodes.execute(
                    job.node_id or "local",
                    "discard_staging",
                    {"root_id": "staging", "path": dl.id},
                    job=job,
                    operation_id=f"discard-{dl.id}-{job.revision}{attempt}",
                    timeout=60,
                )
                discarded = bool(result.get("removed"))
            except NodeError as exc:
                logger.warning("staging for %s not discarded: %s", dl.id, exc)
        await tb.storage.update_download(
            dl.id,
            status=DownloadStatus.ERROR,
            error_message=args.get("reason")
            or ("Removed from download app; its partial files were deleted." if discarded else "Removed from download app; files retained."),
            metadata=dl.metadata,
        )
        if discarded:
            return "Transfer removed and its unfinished files deleted."
        return "Transfer removed. Files that reached the library and their staging originals are preserved."

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
                            "Media is playable, but required subtitles have not met this request's preparation/checking preference. Wait for subtitles_ready or ask the user to get or fix subtitles; do not mark the request complete."
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

    # ─── Scout, pick, review ──────────────────────────────────────────

    def wanted(job):
        """(season, episode) pairs still missing a suitable copy."""
        if job.media_type == "movie":
            return []
        have = {(a.get("season"), a.get("episode")) for a in scoped_assets(tb, job) if suitable(job, a)}
        return [
            (int(season), int(episode))
            for season, episodes in sorted(job.wanted_episodes.items(), key=lambda kv: int(kv[0]))
            for episode in sorted(episodes)
            if (int(season), int(episode)) not in have
        ]

    async def find_releases(ctx, args):
        from . import scout

        job = tb.require_authority(ctx)
        targets = wanted(job)[:12]
        if job.media_type == "tv" and not targets:
            return "Every wanted episode already has a suitable copy in the library."
        season = targets[0][0] if targets else 1
        titles, exclude, namesakes = await scout.titles_for(tb, job, season, targets)
        extra = [str(q)[:120] for q in (args.get("queries") or []) if str(q).strip()][:3]
        rows, searched, dropped, failing = await scout.scout(
            tb, job, targets, titles, extra, exclude=exclude, log=ctx.facts.setdefault("searches", [])
        )
        ctx.facts.update(
            kept=len(rows), dropped=dropped, failing=failing, namesakes=namesakes,
            rows=[{k: row.get(k) for k in ("rid", "name", "seeders", "quality", "coverage", "episode_size", "unlisted")} for row in rows],
        )
        ctx.session.spend.searches += len(searched)
        memo = tb.scouted.setdefault(ctx.session.id, {"vetoes": 0})
        memo.update(rows={row["rid"]: row for row in rows}, order=[row["rid"] for row in rows], targets=targets,
                    namesakes=namesakes, titles=titles)
        names = await episode_titles(job, targets) if job.media_type == "tv" else {}
        wanted_text = ", ".join(
            f"S{s:02d}E{e:02d}" + (f' "{names[(s, e)]}"' if names.get((s, e)) else "") for s, e in targets
        ) or f"{job.title} ({job.year})"
        same = f" Other titles share this name: {namesakes}; make sure a pick is not one of them." if namesakes else ""
        if failing:
            return (
                f"The indexer answered nothing even for the bare title \"{titles[0]}\", so it is failing right now; "
                "this is not proof that no copy exists. Hibernate with wake_me in 15 minutes and call find_releases again."
            )
        if not rows:
            return (
                f"Searched {len(searched)} ways for {wanted_text}: {'; '.join(searched)}. Nothing usable "
                f"({dropped} results did not fit).{same} Try find_releases with other queries (romanised or "
                "English titles, other numbering; every word must appear in a release name), or escalate_model "
                "for a full search."
            )
        return (
            f"Wanted {wanted_text}. Searched: {'; '.join(searched)}. {dropped} other results did not fit.{same}\n"
            + scout.table(rows)
            + "\nPropose the best row with propose_release (one-line reason); a reviewer approves it before it downloads."
        )

    async def propose_release(ctx, args):
        from . import acquisition_review, scout

        job = tb.require_authority(ctx)
        memo = tb.scouted.get(ctx.session.id) or {}
        pick = str(args.get("release", "")).strip().lower()
        lead = re.match(r"(r\d+)\b", pick)
        if lead and lead.group(1) in (memo.get("rows") or {}):
            pick = lead.group(1)  # "r1 files=…": the row id and a note
        if pick not in (memo.get("rows") or {}):
            # A pick named by its release name rather than its row id.
            named = [rid for rid, row in (memo.get("rows") or {}).items()
                     if pick and scout.compact(row["name"]).startswith(scout.compact(pick)[:60])]
            pick = named[0] if len(named) == 1 else pick
        if pick not in (memo.get("rows") or {}):
            raise ToolError("Propose a row id from your latest find_releases list, such as r1.")
        if memo.get("vetoes", 0) >= 2:
            raise ToolError("Two picks were vetoed. Use escalate_model for a full search.")
        rows = [memo["rows"][rid] for rid in memo["order"]]
        titles = await episode_titles(job, memo.get("targets") or [])
        try:
            decision, dollars, model, usage = await acquisition_review.review(
                tb, job, memo.get("targets") or [], rows, pick, str(args.get("reason", "")), titles, memo.get("namesakes", "")
            )
        except Exception as exc:
            raise ToolError(f"The reviewer could not be reached ({str(exc)[:120]}); try again shortly.") from exc
        acquisition_review.charge(ctx.session, model, usage, dollars, tb.store)
        ctx.facts.update(
            pick=pick, name=memo["rows"][pick]["name"], approved=bool(decision.get("approve")),
            reason=decision.get("reason", ""), instead=decision.get("instead", ""), queries=decision.get("queries") or [],
            reviewer=model, review_cost=dollars, vetoes_before=memo.get("vetoes", 0),
        )
        instead = str(decision.get("instead") or "").strip().lower()
        if not decision.get("approve") and instead in memo["rows"] and instead != pick:
            # The reviewer named the row it would approve: take it, without a
            # second round in which the picker could misread the advice.
            ctx.facts.update(chosen_by_reviewer=instead)
            pick = instead
        elif not decision.get("approve"):
            memo["vetoes"] = memo.get("vetoes", 0) + 1
            advice = decision.get("reason") or "The reviewer vetoed this pick."
            queries = [q for q in decision.get("queries") or [] if str(q).strip()][:3]
            if queries:
                advice += " Reviewer suggests searching: " + "; ".join(queries) + " (find_releases with these queries)."
            return "Vetoed. " + advice
        row = memo["rows"][pick]
        ctx.facts["approved"] = not ctx.facts.get("chosen_by_reviewer")
        result = await add(ctx, {"info_hash": row["info_hash"], "name": row["name"][:200], "files": row.get("chosen") or [],
                                 "titles": memo.get("titles") or []})
        memo["vetoes"] = 0
        approved = decision.get("reason", "")
        if ctx.facts.get("chosen_by_reviewer"):
            approved = f"The reviewer chose {pick} instead, and it is downloading: {approved}"
        return {"approved": approved, **(result if isinstance(result, dict) else {"result": result})}

    async def episode_titles(job, targets):
        """TMDB names of the wanted episodes, for the reviewer to match."""
        names = {}
        for season in sorted({s for s, _ in targets}):
            try:
                data = await tb.tmdb_get(f"/tv/{job.tmdb_id}/season/{season}")
            except Exception:
                continue
            for e in data.get("episodes") or []:
                names[(season, e.get("episode_number"))] = e.get("name") or ""
        return names

    async def escalate(ctx, args):
        smart = tb.smart_model()
        if ctx.session.model == smart:
            return "Already on the smart tier with the full search tools."
        ctx.session.model = smart
        return (
            f"Escalated to {smart}: you now search with tpb_search, peek with torrent_peek and add "
            "directly with client_add (files= for packs). Reason noted: " + str(args.get("reason", ""))[:200]
        )

    handlers = {
        "inventory_read": inventory,
        "client_add": add,
        "client_status": status,
        "client_remove": remove,
        "job_close": close,
    }
    text = {"type": "string"}
    return [
        ToolDef(t.name, t.description, t.input_schema, handlers.get(t.name, t.handler))
        for t in fetch_tools(tb)
    ] + [
        ToolDef(
            "find_releases",
            "Search for the wanted episodes (or film) and get a ranked short list: code runs the usual searches, "
            "reads names, peeks inside packs for the right files and drops what cannot fit. queries: up to three "
            "extra searches (alternative titles, other numbering).",
            {"type": "object", "properties": {"queries": {"type": "array", "items": text}}, "required": []},
            find_releases,
        ),
        ToolDef(
            "propose_release",
            "Propose one row from your latest short list with a one-line reason. A reviewer checks the pick; "
            "if approved it starts downloading (only the chosen files of a pack).",
            {"type": "object", "properties": {"release": text, "reason": text}, "required": ["release", "reason"]},
            propose_release,
        ),
        ToolDef(
            "escalate_model",
            "Hand this search to the smart model with full search tools: when the short lists hold nothing usable "
            "after other queries, or two picks were vetoed.",
            {"type": "object", "properties": {"reason": text}, "required": ["reason"]},
            escalate,
        ),
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
        if root == "library":
            # Withdraw a copy this request placed that never passed verification.
            if any(a["path"] == path and a.get("state") == "ready" for a in scoped_assets(tb, job)):
                raise ToolError("Verified library copies are only replaced through upgrade_swap.")
            with tb.accounts.connect() as db:
                placed = db.execute("SELECT 1 FROM publications WHERE job_id=? AND path=?", (job.id, path)).fetchone()
            if not placed:
                raise ToolError("Only a copy this request placed in the library can be withdrawn.")
            result = await operation(ctx, "withdraw", args["path"])
            with tb.accounts.connect() as db:
                db.execute("DELETE FROM publications WHERE job_id=? AND path=?", (job.id, path))
            return result
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
            reasons = unsuitable(job, facts) or ["the measured audio, picture quality or size"]
            raise ToolError("This copy does not meet the request: " + "; ".join(reasons) + ".")
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
            and (
                job.preferences.get("values", {}).get("subtitle_auto_prepare", True)
                or job.preferences.get("values", {}).get("require_subtitles", False)
            )
        ):
            user = tb.accounts.user(job.user_id)
            # Optional preparation belongs to the person even after Fetch closes.
            # The copy is already filed; a subtitle problem must not undo that.
            try:
                await subtitles.enqueue(
                    user,
                    asset,
                    job=(
                        job
                        if job.preferences["values"].get("require_subtitles")
                        else None
                    ),
                    preferences=job.preferences,
                )
            except ToolError as exc:
                logger.warning("subtitle preparation not queued for %s: %s", asset, exc)
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
        if tool.name == "fs_delete":
            description = (
                "Delete junk in this download's staging folder, or withdraw a copy you placed in the library "
                "that failed verification. Verified library copies cannot be deleted."
            )
        out.append(
            ToolDef(
                tool.name, description, schema, handlers.get(tool.name, tool.handler)
            )
        )
    out.append(next(t for t in acquisition_tools(tb) if t.name == "inventory_read"))
    return out
