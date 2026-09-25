"""Resumable administrator setup, based on saved settings and observed storage."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .account_api import administrator
from ..configuration import effective_anthropic_key, effective_tmdb_key
from ..models import SparrowConfig


class SetupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["autopilot", "library"] | None = None
    step: Literal["start", "providers", "storage", "downloads", "review"] | None = None
    deferred: bool | None = None


def setup_status(storage, nodes):
    config = storage.get_config()
    libraries, destinations = [], []
    for node in nodes.list():
        if node["disabled"] or not node["online"]:
            continue
        caps = node["capabilities"]
        roots = {root["id"]: root for root in caps.get("roots", [])}
        library, staging = roots.get("library", {}), roots.get("staging", {})
        if library.get("available") and caps.get("probe"):
            libraries.append(node["name"])
            if (
                library.get("writable")
                and staging.get("available")
                and staging.get("writable")
                and caps.get("download")
            ):
                destinations.append(node["name"])
    providers = bool(effective_tmdb_key(config) and effective_anthropic_key(config))
    return {
        "complete": config.onboarding_complete,
        "deferred": config.onboarding_deferred,
        "mode": config.onboarding_mode,
        "step": config.onboarding_step,
        "tmdb_configured": bool(effective_tmdb_key(config)),
        "reasoning_configured": bool(effective_anthropic_key(config)),
        "libraries": libraries,
        "download_destinations": destinations,
        "can_finish": bool(libraries)
        if config.onboarding_mode == "library"
        else bool(providers and destinations),
    }


def install_onboarding(app, storage, nodes):
    router = APIRouter(prefix="/api/v1/admin/onboarding")

    @router.get("")
    def status(request: Request):
        administrator(request)
        return setup_status(storage, nodes)

    @router.patch("")
    async def update(body: SetupUpdate, request: Request):
        administrator(request)
        config = SparrowConfig.from_dict(storage.get_config().to_dict())
        if body.mode is not None:
            config.onboarding_complete = False
            config.onboarding_deferred = False
        for field, value in body.model_dump(exclude_none=True).items():
            setattr(config, "onboarding_" + field, value)
        await storage.save_config(config)
        return setup_status(storage, nodes)

    @router.post("/finish")
    async def finish(request: Request):
        administrator(request)
        state = setup_status(storage, nodes)
        if not state["can_finish"]:
            raise HTTPException(
                422,
                "Some setup steps still need attention. Check the summary, or choose Finish later.",
            )
        config = SparrowConfig.from_dict(storage.get_config().to_dict())
        config.onboarding_complete = True
        config.onboarding_deferred = False
        config.onboarding_step = "review"
        await storage.save_config(config)
        return setup_status(storage, nodes)

    app.include_router(router)
