"""Household-facing API: scoped catalogue, exact requests and clear recovery."""

import asyncio
from dataclasses import replace
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field

from .account_api import administrator
from .catalogue import Catalogue
from .models import JobStatus, Event, SessionStatus, CaseState
from .node_executor import NodeError
from .runtime import ToolError
from ..configuration import public_config, apply_config_update
from ..models import SparrowConfig
from ..services.library_view import build_library_view


def image_url(path, size="w500"):
    if not path:
        return None
    if path.startswith(("https://", "http://", "/art/")):
        return path
    return f"https://image.tmdb.org/t/p/{size}{path}" if path.startswith("/") else None


def install_product(app, storage, accounts, nodes, get_service):
    from .operations import install_operations

    install_operations(app, storage)
    catalogue = Catalogue(storage, nodes)
    router = APIRouter(prefix="/api/v1")
    job_lock = asyncio.Lock()

    def jobs(user):
        service = get_service()
        if not service:
            return []
        return [
            j
            for j in service.store.get_jobs()
            if (user["role"] == "admin" or j.user_id == user["id"])
            and accounts.can_access(user, j.library_id)
        ]

    def job_for(user, job_id):
        job = next((j for j in jobs(user) if j.id == job_id), None)
        if not job:
            raise HTTPException(404, "Request not found.")
        return job

    async def title(media_type, tmdb_id):
        if media_type not in ("movie", "tv"):
            raise HTTPException(422, "Choose a movie or TV show.")
        cached = catalogue.cached_title(media_type, tmdb_id)
        if cached:
            return cached
        service = get_service()
        if not service:
            raise HTTPException(503, "Sparrow is still starting. Try again shortly.")
        try:
            details = await service.toolbox.tmdb_get(f"/{media_type}/{tmdb_id}")
            return catalogue.cache_title(media_type, tmdb_id, details)
        except ToolError as exc:
            # Existing local metadata is sufficient to browse and watch during an outage.
            item = next(
                (
                    i
                    for i in storage.get_library()
                    if i.tmdb_id == tmdb_id and i.media_type.value == media_type
                ),
                None,
            )
            if item:
                return {
                    "id": tmdb_id,
                    "title": item.title,
                    "name": item.title,
                    "overview": item.overview,
                    "poster_path": item.poster_path,
                    "backdrop_path": item.backdrop_path,
                    "seasons": [
                        {
                            "season_number": int(s),
                            "episode_count": max(map(int, eps), default=0),
                        }
                        for s, eps in item.episodes.items()
                    ],
                    "cached": True,
                }
            raise HTTPException(
                503,
                "Title information is unavailable. Check the catalogue connection in Server settings.",
            ) from exc

    def serialize_item(user, item):
        assets = catalogue.assets(user, item.id)
        ready = [a for a in assets if a["state"] == "ready"]
        subtitles = getattr(app.state, "subtitles", None)
        preferences = accounts.resolve(user["id"])["values"]
        subtitle_pending = bool(
            ready
            and preferences["require_subtitles"]
            and (
                not subtitles
                or any(
                    not subtitles.satisfies(user, a["id"], preferences) for a in ready
                )
            )
        )
        return {
            "id": item.id,
            "title": item.title,
            "media_type": item.media_type.value,
            "tmdb_id": item.tmdb_id,
            "year": item.year,
            "overview": item.overview,
            "poster_url": image_url(item.poster_path),
            "backdrop_url": image_url(item.backdrop_path, "w1280"),
            "library_id": item.metadata.get("library_id", "local"),
            "state": (
                "subtitles_pending"
                if subtitle_pending
                else "ready"
                if ready
                else "unavailable"
                if assets
                else "verifying"
            ),
            "ready_count": len(ready),
            "assets": assets,
            "size_bytes": item.size_bytes,
        }

    @router.get("/suggest")
    async def suggest(request: Request, q: str = Query(min_length=2, max_length=500)):
        service = get_service()
        if not service or not service.toolbox.tmdb_key():
            raise HTTPException(
                503, "Connect TMDB in Server settings to search for titles."
            )
        from ..services.metadata_service import tmdb_quick_suggest

        try:
            return await tmdb_quick_suggest(q, service.toolbox.tmdb_key(), strict=True)
        except Exception as exc:
            raise HTTPException(
                503,
                "Title search is unavailable. Check the catalogue connection and try again.",
            ) from exc

    @router.get("/catalogue")
    def library(request: Request, q: str = "", media_type: str = "", state: str = ""):
        user = request.state.user
        items = [
            serialize_item(user, i)
            for i in storage.get_library()
            if catalogue.visible_item(user, i)
        ]
        return [
            i
            for i in items
            if (not q or q.casefold() in i["title"].casefold())
            and (not media_type or media_type == i["media_type"])
            and (not state or state == i["state"])
        ]

    @router.get("/items/{item_id}")
    def item_detail(item_id: str, request: Request):
        item = catalogue.item(request.state.user, item_id)
        if not item:
            raise HTTPException(404, "This title is not available to your account.")
        return serialize_item(request.state.user, item)

    @router.get("/titles/{media_type}/{tmdb_id}")
    async def title_detail(
        media_type: Literal["movie", "tv"], tmdb_id: int, request: Request
    ):
        details = await title(media_type, tmdb_id)
        user = request.state.user
        items = [
            serialize_item(user, i)
            for i in storage.get_library()
            if i.tmdb_id == tmdb_id
            and i.media_type.value == media_type
            and catalogue.visible_item(user, i)
        ]
        return {
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "title": details.get("title") or details.get("name"),
            "year": (
                details.get("release_date") or details.get("first_air_date") or ""
            )[:4],
            "overview": details.get("overview", ""),
            "poster_url": image_url(details.get("poster_path")),
            "backdrop_url": image_url(details.get("backdrop_path"), "w1280"),
            "seasons": [
                s for s in details.get("seasons", []) if s.get("season_number", 0) >= 0
            ],
            "runtime": details.get("runtime"),
            "items": items,
            "jobs": [
                j.to_dict()
                for j in jobs(user)
                if j.tmdb_id == tmdb_id and j.media_type == media_type
            ],
            "preferences": accounts.resolve(user["id"]),
        }

    @router.get("/titles/tv/{tmdb_id}/seasons/{season}")
    async def season_detail(tmdb_id: int, season: int, request: Request):
        details = await title("tv", tmdb_id)
        cached = details.get("episodes_by_season", {}).get(str(season))
        if cached:
            return cached
        try:
            result = await get_service().toolbox.tmdb_get(
                f"/tv/{tmdb_id}/season/{season}"
            )
            # Re-read before merging so another season response cannot be overwritten.
            details = catalogue.cached_title("tv", tmdb_id) or details
            details.setdefault("episodes_by_season", {})[str(season)] = result.get(
                "episodes", []
            )
            catalogue.cache_title("tv", tmdb_id, details)
            return result.get("episodes", [])
        except ToolError as exc:
            raise HTTPException(
                503,
                "Episode information is unavailable. Existing episodes remain playable.",
            ) from exc

    @router.get("/jobs")
    def list_jobs(request: Request):
        output = []
        for job in jobs(request.state.user):
            value = job.to_dict()
            session = get_service().store.get_session(job.session_id)
            if (
                job.status == JobStatus.ACTIVE
                and session
                and session.status == SessionStatus.HIBERNATING
                and (
                    session.outcome
                    in (
                        CaseState.NEEDS_INPUT,
                        CaseState.BUDGET_LIMITED,
                        CaseState.FAILED,
                    )
                    or any(
                        word in session.wake_reason.lower()
                        for word in ("limit", "budget", "reasoning service", "error")
                    )
                )
            ):
                value["state_line"] = session.wake_reason
                value["needs_attention"] = True
            output.append(value)
        return output

    @router.get("/jobs/{job_id}")
    def job_detail(job_id: str, request: Request):
        job = job_for(request.state.user, job_id)
        service = get_service()
        return {
            "job": job.to_dict(),
            "journal": [j.to_dict() for j in service.store.get_journal(job.id)],
            "downloads": [
                {
                    "id": d.id,
                    "progress": d.progress,
                    "status": d.status.value,
                    "error": d.error_message,
                    "updated": d.stats_updated_at,
                }
                for d in storage.get_all_downloads()
                if d.metadata.get("job_id") == job.id
            ],
        }

    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class NewJob(Input):
        tmdb_id: int = Field(gt=0)
        media_type: Literal["movie", "tv"]
        wanted_episodes: dict[str, list[int]] | None = None
        preferences: dict = Field(default_factory=dict)
        node_id: str = "local"
        monitoring: Literal["", "exact", "keep_current", "seasons", "backfill"] = ""

    @router.post("/jobs")
    async def create_job(body: NewJob, request: Request):
        user = request.state.user
        if user["role"] not in ("admin", "requester"):
            raise HTTPException(
                403, "Your account can watch; ask an administrator for request access."
            )
        if not accounts.can_access(user, body.node_id):
            raise HTTPException(
                403, "This storage destination is not available to your account."
            )
        node = nodes.info(body.node_id)
        if not node or node["disabled"]:
            raise HTTPException(422, "Choose a paired storage destination.")
        async with job_lock:
            try:
                job = await get_service().create_job(
                    body.tmdb_id,
                    body.wanted_episodes,
                    media_type=body.media_type,
                    user_id=user["id"],
                    preference_overrides=body.preferences,
                    monitoring=body.monitoring,
                    library_id=body.node_id,
                    node_id="" if body.node_id == "local" else body.node_id,
                )
            except (ValueError, ToolError) as exc:
                raise HTTPException(422, str(exc)) from exc
        return job.to_dict()

    @router.post("/jobs/{job_id}/{action}")
    async def control(
        job_id: str,
        action: Literal["pause", "resume", "cancel", "retry"],
        request: Request,
    ):
        job = job_for(request.state.user, job_id)
        if request.state.user["role"] not in ("admin", "requester"):
            raise HTTPException(403, "Request access is required.")
        service = get_service()
        method = {
            "pause": service.pause_job,
            "resume": service.resume_job,
            "cancel": service.cancel_job,
            "retry": service.resume_job,
        }[action]
        return (await method(job.id)).to_dict()

    class Scan(Input):
        node_id: str = "local"
        root_id: Literal["library"] = "library"
        path: str = ""

    class Selection(Input):
        id: str
        title: str = Field(default="", max_length=300)
        tmdb_id: int | None = Field(default=None, gt=0)
        media_type: Literal["movie", "tv"]
        season: int | None = Field(default=None, ge=0)
        episode: int | None = Field(default=None, ge=1)

    class Confirm(Input):
        selections: list[Selection] = Field(min_length=1, max_length=100)

    @router.post("/admin/imports/preview")
    async def preview(body: Scan, request: Request):
        administrator(request)
        try:
            return await catalogue.scan(body.node_id, body.root_id, body.path)
        except NodeError as exc:
            storage.operations.record("import_failed", library_id=body.node_id)
            raise HTTPException(422, str(exc)) from exc

    @router.post("/admin/imports/{scan_id}/confirm")
    async def confirm(scan_id: str, body: Confirm, request: Request):
        administrator(request)

        async def episode_facts(tmdb_id, season):
            return await get_service().toolbox.tmdb_get(
                f"/tv/{tmdb_id}/season/{season}"
            )

        try:
            return await catalogue.confirm_import(
                scan_id, [s.model_dump() for s in body.selections], title, episode_facts
            )
        except NodeError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/admin/config")
    def config(request: Request):
        administrator(request)
        return public_config(storage.get_config())

    @router.patch("/admin/config")
    async def update_config(request: Request):
        administrator(request)
        from ..main import ConfigUpdate

        try:
            body = ConfigUpdate.model_validate(await request.json())
            config = SparrowConfig.from_dict(storage.get_config().to_dict())
            config = apply_config_update(config, body.model_dump(exclude_unset=True))
            await storage.save_config(config)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return public_config(config)

    app.include_router(router)
    return catalogue
