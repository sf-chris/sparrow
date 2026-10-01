"""
AgentService — wires the runtime, tool belt, and plumbing together.

Plumbing (the poller, the timer scan, the event routing) feeds agents and
makes ZERO decisions. It observes ("this torrent hasn't moved in 15
minutes", "these files landed") and wakes the session that owns the job.
What to do about it is the agent's call.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from ..models import DownloadStatus, MediaType, TorrentClientType
from ..storage import Storage
from ..services.torrent_client import TorrentManager, start_configured_client
from . import prompts
from .models import (
    AgentKind,
    CaseState,
    AgentSession,
    Event,
    Job,
    JobStatus,
    Mandate,
    MonitoringMode,
    SessionStatus,
    Urgency,
)
from .runtime import AgentRuntime, AgentSpec, ToolError, spend_snapshot
from .store import AgentStore
from .accounts import Accounts
from .tools import (
    Toolbox,
    journal_tool,
    memory_tools,
    remove_download_staging,
    tmdb_tools,
    wake_tool,
)

from .node_tools import acquisition_tools, storage_tools, components

logger = logging.getLogger("sparrow.agents")

DEFAULT_SMART_MODEL = "claude-sonnet-5"
DEFAULT_CHEAP_MODEL = "claude-haiku-4-5"

POLL_INTERVAL = 15.0  # plumbing poll cadence (seconds)
STALL_AFTER = 15 * 60  # no progress for this long = stalled
TIMER_INTERVAL = 20.0  # wake-timer scan cadence
CLIENT_RECOVERY_COOLDOWN = 5 * 60

TOOL_ACTIVITY_LABELS = {
    "journal_write": "publishing a progress update",
    "memory_read": "reading previous notes",
    "memory_write": "saving a useful lesson",
    "wake_me": "setting the next check",
    "tmdb_search": "checking title information",
    "tmdb_show": "checking the show record",
    "tmdb_season": "checking episodes and air dates",
    "tmdb_titles": "checking alternative titles",
    "tpb_search": "searching available sources",
    "torrent_peek": "inspecting a candidate's file list",
    "client_add": "sending a download to the download app",
    "client_status": "checking download progress",
    "client_remove": "removing an unsuitable download",
    "triage_parse": "sorting search results",
    "inventory_read": "checking the library inventory",
    "job_close": "reconciling the finished job",
    "fs_list": "listing landed files",
    "fs_probe": "verifying a media file",
    "fs_move": "placing a verified media file",
    "fs_delete": "removing staging junk",
    "upgrade_swap": "replacing a lower-quality copy",
    "inventory_write": "updating the library inventory",
    "report_to_fetch": "reporting the media result",
    "escalate_model": "escalating a difficult media decision",
    "session_done": "closing the media session",
    "library_overview": "reviewing the whole library",
    "spawn_job": "starting a library-maintenance job",
}


def _diagnostic_value(value):
    """Keep execution details useful without ever persisting credentials."""
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(
                secret in lower for secret in ("password", "api_key", "token", "secret")
            ):
                safe[key] = "<redacted>"
            else:
                safe[key] = _diagnostic_value(item)
        return safe
    if isinstance(value, list):
        return [_diagnostic_value(item) for item in value]
    return value


class AgentService:
    def __init__(self, storage: Storage, data_dir: str, broadcast: Callable):
        self.storage = storage
        self.store = AgentStore(data_dir)
        self.accounts = Accounts(data_dir)
        self.broadcast = broadcast
        self.toolbox = Toolbox(storage, self.store, self.emit, broadcast)
        self.runtime = AgentRuntime(
            self.store,
            api_key_getter=lambda: (
                storage.get_config().anthropic_api_key
                or os.getenv("ANTHROPIC_API_KEY", "")
            ),
            on_session_change=self._session_changed,
            on_tool_activity=self._tool_activity,
        )
        self.runtime.policy_getter = lambda: self.accounts.server_settings()["policy"]
        self._register_specs()
        from .curation import Curation

        self.curation = Curation(self)
        self._create_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._wake_tasks: set[asyncio.Task] = set()
        self._progress_seen: dict[
            str, tuple[float, float]
        ] = {}  # dl_id -> (progress, ts)
        self._stall_flagged: set[str] = set()
        self._client_up: Optional[bool] = None
        self._client_recovery_after = 0.0
        recent_recovery = next(
            (
                event
                for event in storage.get_activity(limit=20)
                if event.kind == "client_recovery"
            ),
            None,
        )
        self._client_recovery_message = (
            recent_recovery.message if recent_recovery else ""
        )

    # ─── Specs ───────────────────────────────────────────────────────────

    def smart_model(self) -> str:
        cfg = self.storage.get_config()
        return (
            os.getenv("SPARROW_SMART_MODEL") or cfg.smart_model or DEFAULT_SMART_MODEL
        )

    def cheap_model(self) -> str:
        cfg = self.storage.get_config()
        return (
            os.getenv("SPARROW_CHEAP_MODEL") or cfg.cheap_model or DEFAULT_CHEAP_MODEL
        )

    def _register_specs(self) -> None:
        tb = self.toolbox

        async def fetch_system(session: AgentSession) -> str:
            job = self.store.get_job(session.job_id)
            return (
                prompts.fetch_system(session, job, "", cfg=self.storage.get_config())
                + "\nEffective request settings (authoritative):\n"
                + json.dumps(job.preferences if job else {}, sort_keys=True)
            )

        async def media_system(session: AgentSession) -> str:
            job = self.store.get_job(session.job_id) if session.job_id else None
            dl = self.storage.get_download(session.download_id)
            cfg = self.storage.get_config()
            staging = f"staging/{dl.id}" if dl else "staging"
            return (
                prompts.media_system(
                    session, job, dl.name if dl else "(unknown)", staging, "library"
                )
                + "\nEffective request settings (authoritative):\n"
                + json.dumps(job.preferences if job else {}, sort_keys=True)
            )

        self.runtime.register(
            AgentSpec(
                kind=AgentKind.FETCH.value,
                model=self.smart_model,
                system=fetch_system,
                tools=lambda s: [
                    journal_tool(tb, "fetch"),
                    wake_tool(tb),
                    *memory_tools(tb),
                    *tmdb_tools(tb),
                    *acquisition_tools(tb),
                ],
            )
        )
        self.runtime.register(
            AgentSpec(
                kind=AgentKind.MEDIA.value,
                model=self.cheap_model,
                system=media_system,
                tools=lambda s: [
                    journal_tool(tb, "media"),
                    wake_tool(tb),
                    *memory_tools(tb),
                    *tmdb_tools(tb),
                    *storage_tools(tb),
                ],
            )
        )

    async def _session_changed(self, session: AgentSession) -> None:
        await self.broadcast(
            {
                "type": "session_update",
                "data": {
                    "id": session.id,
                    "agent": session.agent.value,
                    "job_id": session.job_id,
                    "status": session.status.value,
                    "outcome": session.outcome.value,
                    "wake_at": session.wake_at,
                    "wake_reason": session.wake_reason,
                    "spend": spend_snapshot(session),
                },
            }
        )

    async def _tool_activity(
        self,
        session: AgentSession,
        tool_name: str,
        phase: str,
        args: dict,
        content: str,
        is_error: bool,
    ) -> None:
        label = TOOL_ACTIVITY_LABELS.get(tool_name, tool_name.replace("_", " "))
        prefix = {"started": "Started", "completed": "Finished", "failed": "Failed"}[
            phase
        ]
        job = self.store.get_job(session.job_id) if session.job_id else None
        detail_parts = [f"Tool: {tool_name}"]
        if args:
            rendered_args = json.dumps(_diagnostic_value(args), indent=2, default=str)
            detail_parts.append(f"Input:\n{rendered_args[:6000]}")
        if content:
            detail_parts.append(f"Result:\n{content[:12000]}")
        event = await self.storage.add_activity(
            "agent_step",
            f"{prefix} {label}.",
            detail="\n\n".join(detail_parts),
            request_id=session.job_id,
            tmdb_id=job.tmdb_id if job else None,
            level="warning" if is_error else "info",
        )
        await self.broadcast({"type": "activity", "data": event.to_dict()})

    def session_trace(
        self, session: Optional[AgentSession], limit: int = 100
    ) -> list[dict]:
        """Recover detailed steps from durable model history for pre-logging sessions."""
        if not session:
            return []
        entries: list[dict] = []
        by_tool_use: dict[str, dict] = {}
        for message in session.messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            if message.get("role") == "assistant":
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_name = str(block.get("name") or "unknown_tool")
                    entry = {
                        "id": str(block.get("id") or f"trace-{len(entries)}"),
                        "tool": tool_name,
                        "message": TOOL_ACTIVITY_LABELS.get(
                            tool_name, tool_name.replace("_", " ")
                        ).capitalize()
                        + ".",
                        "input": _diagnostic_value(block.get("input") or {}),
                        "result": "",
                        "is_error": False,
                    }
                    entries.append(entry)
                    by_tool_use[entry["id"]] = entry
            elif message.get("role") == "user":
                for block in content:
                    if (
                        not isinstance(block, dict)
                        or block.get("type") != "tool_result"
                    ):
                        continue
                    entry = by_tool_use.get(str(block.get("tool_use_id") or ""))
                    if entry is not None:
                        entry["result"] = str(block.get("content") or "")[:12000]
                        entry["is_error"] = bool(block.get("is_error"))
        return entries[-limit:]

    def prompt_previews(self) -> list[dict]:
        """Render the actual standing prompts with current local context."""
        jobs = self.store.get_jobs()
        job = (
            jobs[0]
            if jobs
            else Job(
                tmdb_id=0,
                media_type="tv",
                title="<selected title>",
                wanted_episodes={"1": [1, 2]},
                preferred_quality=self.storage.get_config().quality_preference.value,
            )
        )
        cfg = self.storage.get_config()
        fetch_session = AgentSession(
            agent=AgentKind.FETCH, job_id=job.id, model=self.smart_model()
        )
        media_session = AgentSession(
            agent=AgentKind.MEDIA, job_id=job.id, model=self.cheap_model()
        )
        librarian_session = AgentSession(
            agent=AgentKind.LIBRARIAN, model=self.cheap_model()
        )
        toolsets = {
            "fetch": self.runtime.tools_for(fetch_session),
            "media": self.runtime.tools_for(media_session),
            "librarian": self.runtime.tools_for(librarian_session),
        }

        def tools_for(agent: str) -> list[dict]:
            return [tool.to_api() for tool in toolsets[agent]]

        return [
            {
                "agent": "fetch",
                "label": "Fetch Agent",
                "model": self.smart_model(),
                "context": f"Rendered with the latest job contract: {job.title}",
                "prompt": prompts.fetch_system(
                    fetch_session, job, "", cfg=self.storage.get_config()
                ),
                "tools": tools_for("fetch"),
            },
            {
                "agent": "media",
                "label": "Media Agent",
                "model": self.cheap_model(),
                "context": "Rendered with current staging/library paths and a placeholder landed file.",
                "prompt": prompts.media_system(
                    media_session,
                    job,
                    "<landed download supplied at runtime>",
                    cfg.staging_dir,
                    cfg.library_dir,
                ),
                "tools": tools_for("media"),
            },
            {
                "agent": "librarian",
                "label": "Librarian",
                "model": self.cheap_model(),
                "context": "Standing prompt for one person and one subscription.",
                "prompt": self.curation.system_prompt(),
                "tools": tools_for("librarian"),
            },
        ]

    # ─── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._timer_loop()),
            asyncio.create_task(self._plumbing_loop()),
            asyncio.create_task(self._boot_recovery()),
            asyncio.create_task(self.curation.loop()),
        ]

    def stop(self) -> None:
        for t in [*self._tasks, *self._wake_tasks]:
            t.cancel()

    async def shutdown(self):
        self.stop()
        await asyncio.gather(*self._tasks, *self._wake_tasks, return_exceptions=True)
        if hasattr(self.toolbox, "nodes"):
            await self.toolbox.nodes.local().shutdown()

    async def _boot_recovery(self) -> None:
        await asyncio.sleep(5)
        self._normalize_closed_sessions()
        await self._reconcile_completed_media_sessions()
        # Route persisted facts first, then recover accepted-but-interrupted turns.
        for event in self.store.unrouted_events():
            await self.emit(event)
        pending = set(self.store.pending_session_ids())
        for s in self.store.get_sessions(open_only=True):
            if s.agent == AgentKind.LIBRARIAN:
                row = self.curation.row(s.download_id)
                if row and row["data"].get("session_id") == s.id:
                    # Curation gathers current authority/storage facts before retrying.
                    self.curation.save(
                        row, next_check=s.wake_at if s.wake_at > time.time() else 0
                    )
                continue
            if s.id in pending:
                self._resume_soon(s.id)
            elif s.status == SessionStatus.RUNNING:
                await self.emit(
                    Event(
                        kind="restart",
                        session_id=s.id,
                        payload={
                            "description": "Sparrow restarted during this review. Inspect saved tool outcomes and current evidence before continuing."
                        },
                    )
                )
        self.curation.migrate()

    def _normalize_closed_sessions(self) -> None:
        """Closed sessions never retain timers or misleading wait reasons."""
        for session in self.store.get_sessions():
            if session.status == SessionStatus.CLOSED and (
                session.wake_at or session.wake_reason
            ):
                session.wake_at = 0.0
                session.wake_reason = ""
                self.store.save_session(session)

    async def _reconcile_completed_media_sessions(self) -> None:
        """Repair transfer bookkeeping after a crash between placement and close.

        New inventory rows carry an exact source_download_id and use the normal
        evidence path. The timestamp fallback is intentionally narrow and only
        migrates alpha-era sessions that closed beside a verified inventory
        write before the source marker existed.
        """
        for session in self.store.get_sessions(AgentKind.MEDIA):
            if session.status != SessionStatus.CLOSED or not session.download_id:
                continue
            download = self.storage.get_download(session.download_id)
            if not download:
                continue
            await self.toolbox.refresh_missing_verified_quality(session)
            if download.status == DownloadStatus.ORGANIZED:
                continue
            if await self.toolbox.mark_session_download_organized(session):
                continue
            job = self.store.get_job(session.job_id) if session.job_id else None
            item = (
                self.toolbox.library_item_for(job.tmdb_id, job.media_type)
                if job
                else None
            )
            if not job or not item or not session.closed_at:
                continue
            if job.media_type == MediaType.MOVIE.value:
                added_at = float(item.metadata.get("added_at") or 0)
                verified = bool(item.metadata.get("verified"))
                path = item.path
            else:
                candidates = [
                    episode
                    for season in item.episodes.values()
                    for episode in season.values()
                    if episode.get("verified")
                ]
                newest = max(
                    candidates, key=lambda entry: entry.get("added_at", 0), default={}
                )
                added_at = float(newest.get("added_at") or 0)
                verified = bool(newest)
                path = item.path
            if (
                verified
                and Path(path).exists()
                and added_at
                and abs(session.closed_at - added_at) <= 15 * 60
            ):
                if job.media_type == MediaType.MOVIE.value:
                    item.metadata["source_download_id"] = session.download_id
                    await self.storage.update_library_item(
                        item.id, metadata=item.metadata
                    )
                await self.storage.update_download(
                    download.id,
                    status=DownloadStatus.ORGANIZED,
                    library_path=path,
                    completed_at=download.completed_at or session.closed_at,
                )
                refreshed = self.storage.get_download(download.id)
                await self.broadcast(
                    {
                        "type": "download_update",
                        "data": (refreshed or download).to_dict(),
                    }
                )

    # ─── Event routing (zero decisions, only delivery) ───────────────────

    async def emit(self, event: Event) -> None:
        self.store.enqueue_event(event)
        codes = {
            "download_stalled": "download_stalled",
            "download_recovered": "download_recovered",
            "files_landed": "media_checking",
            "client_down": "client_down",
            "client_recovered": "client_recovered",
        }
        if event.kind in codes:
            job = self.store.get_job(event.job_id) if event.job_id else None
            if job:
                self.storage.operations.job(
                    codes[event.kind], job, subject=event.download_id
                )
        try:
            await self._route(event)
            self.store.mark_routed(event)
        except Exception:
            logger.exception("event routing failed for %s", event.kind)

    async def _route(self, event: Event) -> None:
        # Wakes are fire-and-forget: a wake can run a whole agent turn, and
        # the caller (an API handler, the plumbing loop, or another agent's
        # tool call) must never block on it.
        if event.session_id:
            self._wake_soon(event.session_id, event)
            return

        if event.kind == "files_landed":
            await self._spawn_media(event)
            return

        if event.job_id:
            job = self.store.get_job(event.job_id)
            if not job:
                return
            if job.status == JobStatus.PAUSED and event.kind != "resume":
                return
            if job.session_id:
                self._wake_soon(job.session_id, event)
            return

        if event.kind in ("episode_aired", "library_changed"):
            for row in self.curation.rows():
                self.curation.save(row, next_check=0)

    def _wake_soon(self, session_id: str, event: Event) -> None:
        self.store.enqueue_delivery(session_id, event)
        self._resume_soon(session_id)

    def _resume_soon(self, session_id: str) -> None:
        async def _go():
            try:
                async with self.runtime._lock(session_id):
                    await self.runtime._run(session_id)
            except Exception:
                logger.exception("wake failed (session %s)", session_id)

        task = asyncio.create_task(_go())
        self._wake_tasks.add(task)
        task.add_done_callback(self._wake_tasks.discard)

    async def _spawn_media(self, event: Event) -> None:
        """A download landed: one Media Agent session per landed download."""
        existing = [
            s
            for s in self.store.get_sessions(AgentKind.MEDIA)
            if s.download_id == event.download_id
        ]
        if existing:
            self._wake_soon(existing[0].id, event)
            return
        job = self.store.get_job(event.job_id)
        if job and job.status != JobStatus.ACTIVE:
            return
        session = AgentSession(
            agent=AgentKind.MEDIA,
            job_id=event.job_id,
            job_revision=job.revision if job else 1,
            download_id=event.download_id,
            model=self.cheap_model(),
        )
        self.store.save_session(session)
        self._wake_soon(session.id, event)

    # ─── Mandates ────────────────────────────────────────────────────────

    def record_user_request(
        self, tmdb_id: int, media_type: str, wanted_episodes: dict, monitoring: str = ""
    ) -> Mandate:
        """Fold an explicit user request into the show's standing authority."""
        mandate = self.store.get_mandate(tmdb_id, media_type) or Mandate(
            tmdb_id=tmdb_id, media_type=media_type
        )
        mandate.merge_request(wanted_episodes)
        if monitoring:
            mandate.mode = MonitoringMode(monitoring)
            mandate.granted_at = time.time()
        return self.store.save_mandate(mandate)

    def set_monitoring(
        self, tmdb_id: int, media_type: str, mode: str, seasons: Optional[list] = None
    ) -> Mandate:
        """User changed the monitoring scope from Preferences."""
        mandate = self.store.get_mandate(tmdb_id, media_type) or Mandate(
            tmdb_id=tmdb_id, media_type=media_type
        )
        mandate.mode = MonitoringMode(mode)
        mandate.seasons = sorted({int(s) for s in (seasons or [])})
        mandate.granted_at = time.time()
        mandate.updated_at = time.time()
        return self.store.save_mandate(mandate)

    # ─── Jobs ────────────────────────────────────────────────────────────

    async def create_job(self, *args, **kwargs) -> Job:
        async with self._create_lock:
            return await self._create_job(*args, **kwargs)

    async def _create_job(
        self,
        tmdb_id: int,
        wanted_episodes: Optional[dict] = None,
        media_type: str = "tv",
        origin: str = "user",
        preferred_quality: str = "",
        min_quality: str = "",
        audio_pref: str = "any",
        urgency: str = "soon",
        monitoring: str = "",
        user_id: str = "",
        preference_overrides: Optional[dict] = None,
        library_id: str = "local",
        node_id: str = "",
        authority_check=None,
    ) -> Job:
        cfg = self.storage.get_config()
        contract = {}
        if user_id:
            user = self.accounts.user(user_id)
            if (
                not user
                or user["role"] not in ("admin", "requester")
                or not self.accounts.can_access(user, library_id)
            ):
                raise ValueError("This account cannot acquire media in that library.")
            contract = self.accounts.resolve(user_id, preference_overrides)
            values = contract["values"]
            preferred_quality, min_quality = (
                values["preferred_quality"],
                values["min_quality"],
            )
            audio_pref, urgency = values["audio_pref"], values["urgency"]
            monitoring = monitoring or values["monitoring"]
        elif self.accounts.has_users():
            raise ValueError("A requesting account is required.")
        details = await self.toolbox.tmdb_get(f"/{media_type}/{tmdb_id}")
        title = details.get("name") or details.get("title") or f"tmdb-{tmdb_id}"
        year_raw = (details.get("first_air_date") or details.get("release_date") or "")[
            :4
        ]

        if media_type == "tv" and not wanted_episodes:
            raise ValueError("Choose the exact episodes or seasons to request.")
        if media_type == "tv":
            normalized = {}
            for season, episodes in wanted_episodes.items():
                number = int(season)
                chosen = sorted({int(e) for e in episodes})
                if number < 0 or not chosen or any(e < 1 for e in chosen):
                    raise ValueError(
                        "Each selected season needs valid episode numbers."
                    )
                facts = await self.toolbox.tmdb_get(f"/tv/{tmdb_id}/season/{number}")
                known = {e["episode_number"] for e in facts.get("episodes", [])}
                if not set(chosen) <= known:
                    raise ValueError(
                        f"The requested episodes do not exist in season {number}."
                    )
                normalized[str(number)] = chosen
            wanted_episodes = normalized

        def check_requester():
            if authority_check:
                authority_check()
            if user_id:
                principal = self.accounts.user(user_id)
                if (
                    not principal
                    or principal["disabled"]
                    or principal["role"] not in ("admin", "requester")
                    or not self.accounts.can_access(principal, library_id)
                ):
                    raise ValueError(
                        "Request access changed while catalogue information was loading."
                    )

        check_requester()
        if user_id and origin == "user":
            self.curation.record(
                user_id,
                tmdb_id,
                media_type,
                library_id,
                wanted_episodes or {},
                monitoring,
                contract,
            )
        elif user_id:
            await self.curation.enforce(
                user_id, tmdb_id, media_type, library_id, wanted_episodes or {}
            )
        elif origin == "user":
            # User requests create/extend the standing authority the tool
            # layer enforces against every agent-originated job.
            self.record_user_request(
                tmdb_id, media_type, wanted_episodes or {}, monitoring=monitoring
            )
        else:
            if media_type == "tv" and not wanted_episodes:
                raise ValueError(
                    "Agent-originated jobs must name exact episodes; a blanket "
                    "request is a user decision."
                )
            await self.toolbox.enforce_mandate(
                tmdb_id, media_type, wanted_episodes or {}, origin
            )

        check_requester()
        existing = next(
            (
                j
                for j in self.store.get_jobs()
                if user_id
                and j.user_id == user_id
                and j.tmdb_id == tmdb_id
                and j.media_type == media_type
                and j.library_id == library_id
                and j.status in (JobStatus.ACTIVE, JobStatus.PAUSED)
            ),
            None,
        )
        if existing:
            merged = {s: list(eps) for s, eps in existing.wanted_episodes.items()}
            if origin != "user" and contract.get("values") != existing.preferences.get(
                "values"
            ):
                raise ToolError(
                    "This title has an active request with different preferences. Wait for that request to finish before starting the new care contract."
                )
            for season, episodes in (wanted_episodes or {}).items():
                merged[season] = sorted(set(merged.get(season, [])) | set(episodes))
            if merged == existing.wanted_episodes and contract.get(
                "values"
            ) == existing.preferences.get("values"):
                return existing
            was_paused = existing.status == JobStatus.PAUSED
            existing = await self.pause_job(existing.id)
            check_requester()
            existing.wanted_episodes = merged
            existing.preferences = contract
            existing.preferred_quality = preferred_quality
            existing.min_quality = min_quality
            existing.audio_pref = audio_pref
            existing.urgency = Urgency(urgency)
            existing.state_line = "Request updated."
            self.store.save_job(existing)
            await self.broadcast({"type": "job_update", "data": existing.to_dict()})
            return existing if was_paused else await self.resume_job(existing.id)

        job = Job(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title=title,
            user_id=user_id,
            library_id=library_id,
            node_id=node_id,
            preferences=contract,
            year=int(year_raw) if year_raw.isdigit() else None,
            poster_path=details.get("poster_path") or "",
            wanted_episodes=wanted_episodes or {},
            preferred_quality=preferred_quality or cfg.quality_preference.value,
            min_quality=min_quality or ("720p" if urgency != "whenever" else "1080p"),
            audio_pref=audio_pref,
            urgency=Urgency(urgency),
            origin=origin,
            original_language=details.get("original_language", ""),
            state_line="Starting…",
        )
        session = AgentSession(
            agent=AgentKind.FETCH,
            job_id=job.id,
            user_id=user_id,
            model=self.smart_model(),
        )
        job.session_id = session.id
        self.store.save_session(session)
        self.store.save_job(job)
        await self.broadcast({"type": "job_added", "data": job.to_dict()})
        await self.emit(
            Event(
                kind="job_created",
                job_id=job.id,
                payload={"description": f"New job: {title} — get to work."},
            )
        )
        return job

    async def nudge(self, job_id: str) -> None:
        await self.emit(
            Event(
                kind="nudge",
                job_id=job_id,
                payload={
                    "description": "The user asked for a check right now. Reassess and report."
                },
            )
        )

    def _job_downloads(self, job_id: str, statuses: tuple) -> list:
        return [
            dl
            for dl in self.storage.get_all_downloads()
            if dl.metadata.get("job_id") == job_id and dl.status in statuses
        ]

    async def _connected_manager(self) -> Optional[TorrentManager]:
        cfg = self.storage.get_config()
        if cfg.torrent_client.type == TorrentClientType.NONE:
            return None
        mgr = TorrentManager(cfg.torrent_client)
        return mgr if await mgr.connect() else None

    async def pause_job(self, job_id: str):
        from .transfer_control import control_job

        return await control_job(self, job_id, "pause")

    async def resume_job(self, job_id: str):
        from .transfer_control import control_job

        return await control_job(self, job_id, "resume")

    async def cancel_job(self, job_id: str):
        from .transfer_control import control_job

        return await control_job(self, job_id, "cancel")

    # ─── Plumbing loops ──────────────────────────────────────────────────

    async def _timer_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(TIMER_INTERVAL)
                for event in self.store.unrouted_events():
                    await self.emit(event)
                for s in self.store.due_sessions():
                    if s.agent == AgentKind.LIBRARIAN:
                        await self.curation.check(s.download_id)
                        continue
                    job = self.store.get_job(s.job_id) if s.job_id else None
                    if job and job.status == JobStatus.PAUSED:
                        # Paused jobs don't wake on timers; the resume event
                        # will wake the session instead.
                        s.wake_at = 0.0
                        self.store.save_session(s)
                        continue
                    await self.emit(
                        Event(
                            kind="timer",
                            session_id=s.id,
                            payload={
                                "description": f"Your wake timer fired. You were waiting on: "
                                f"{s.wake_reason or '(no reason recorded)'}"
                            },
                        )
                    )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("timer loop error")

    async def _plumbing_loop(self) -> None:
        """Watch the torrent client and staging: emit facts, decide nothing."""
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL)
                await self.reconcile_transfers()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("plumbing loop error")

    async def reconcile_transfers(self):
        from .transfer_control import apply_control
        from .node_executor import NodeError

        nodes, _ = components(self.toolbox)
        nodes.observe_availability()
        cfg = self.storage.get_config()
        if cfg.seeding_ratio_limit > 0 or cfg.seeding_time_hours > 0:
            for download in self.storage.get_all_downloads():
                if (
                    not download.metadata.get("node_id")
                    or download.metadata.get("seeding_stopped")
                    or download.status
                    not in (
                        DownloadStatus.COMPLETED,
                        DownloadStatus.ORGANIZED,
                        DownloadStatus.SEEDING,
                    )
                ):
                    continue
                owner_job = self.store.get_job(download.metadata.get("job_id", ""))
                if not owner_job:
                    continue
                try:
                    state = await nodes.execute(
                        download.metadata["node_id"],
                        "download_status",
                        {"hash": download.torrent_hash},
                        timeout=10,
                    )
                    if state and (
                        (
                            cfg.seeding_ratio_limit > 0
                            and float(state.get("upload_ratio") or 0)
                            >= cfg.seeding_ratio_limit
                        )
                        or (
                            cfg.seeding_time_hours > 0
                            and float(state.get("seeding_time") or 0)
                            >= cfg.seeding_time_hours * 3600
                        )
                    ):
                        await nodes.execute(
                            download.metadata["node_id"],
                            "download_stop",
                            {"hash": download.torrent_hash},
                            job=owner_job,
                            operation_id=f"seed-stop-{download.id}-{owner_job.revision}",
                            timeout=15,
                        )
                        download.metadata["seeding_stopped"] = True
                        await self.storage.update_download(
                            download.id, metadata=download.metadata
                        )
                except NodeError:
                    pass
        manager = None
        if cfg.torrent_client.type != TorrentClientType.NONE:
            manager = TorrentManager(cfg.torrent_client)
            up = await manager.connect()
            if not up:
                up = await self._recover_client(cfg.torrent_client, manager)
            await self._client_transitions(up)
            if not up:
                manager = None
        for dl in self.storage.get_all_downloads():
            if not dl.metadata.get("agent_managed"):
                continue
            job = self.store.get_job(dl.metadata.get("job_id", ""))
            if not job:
                continue
            if dl.metadata.get("desired_control"):
                await apply_control(self, job, dl)
                continue
            if job.status != JobStatus.ACTIVE:
                continue
            if dl.status in (
                DownloadStatus.ORGANIZED,
                DownloadStatus.ERROR,
                DownloadStatus.COMPLETED,
                DownloadStatus.PAUSED,
            ):
                continue
            try:
                if dl.metadata.get("node_id"):
                    info = nodes.info(dl.metadata["node_id"])
                    if not info or not info["online"]:
                        continue
                    st = await nodes.execute(
                        dl.metadata["node_id"],
                        "download_status",
                        {"hash": dl.torrent_hash},
                        timeout=10,
                    )
                    if st is None and dl.status == DownloadStatus.QUEUED:
                        # Idempotent recovery of a reservation accepted before a restart or lost response.
                        await nodes.execute(
                            dl.metadata["node_id"],
                            "download_add",
                            {
                                "info_hash": dl.torrent_hash,
                                "name": dl.name,
                                "path": dl.id,
                            },
                            job=job,
                            timeout=20,
                        )
                        continue
                elif manager:
                    if dl.metadata.get("wanted_files") and not dl.metadata.get("selection"):
                        # A pack choice waits for the torrent's file list.
                        await self.toolbox.apply_file_selection(dl)
                        dl = self.storage.get_download(dl.id) or dl
                        if dl.status == DownloadStatus.ERROR:
                            continue
                    st = await manager.get_torrent_status(dl.torrent_hash)
                else:
                    continue
                current = self.store.get_job(job.id)
                if (
                    current.status != JobStatus.ACTIVE
                    or current.revision != job.revision
                ):
                    continue
                if st is None:
                    continue
                progress = float(st.get("progress") or 0.0)
                stats = {
                    "progress": progress,
                    "size_bytes": int(st.get("size_bytes") or 0),
                    "downloaded_bytes": int(st.get("downloaded_bytes") or 0),
                    "download_speed": int(st.get("download_speed") or 0),
                    "eta_seconds": int(
                        st.get("eta_seconds")
                        if st.get("eta_seconds") is not None
                        else -1
                    ),
                    "stats_updated_at": time.time(),
                }

                if progress >= 1.0 and not dl.metadata.get("landed_emitted"):
                    landing = Event(
                        kind="files_landed",
                        job_id=dl.metadata.get("job_id", ""),
                        download_id=dl.id,
                        payload={
                            "description": f'Download finished: "{dl.name}". '
                            "Files are in staging."
                        },
                    )
                    self.store.enqueue_event(landing)
                    dl.metadata["landed_emitted"] = True
                    await self.storage.update_download(
                        dl.id,
                        metadata=dl.metadata,
                        status=DownloadStatus.COMPLETED,
                        **{**stats, "progress": 1.0},
                    )
                    refreshed = self.storage.get_download(dl.id)
                    await self.broadcast(
                        {"type": "download_update", "data": (refreshed or dl).to_dict()}
                    )
                    await self.emit(landing)
                    continue

                # Persist and broadcast live numbers every poll — active
                # downloads must never sit at 0% until completion.
                await self.storage.update_download(dl.id, **stats)
                refreshed = self.storage.get_download(dl.id)
                await self.broadcast(
                    {"type": "download_update", "data": (refreshed or dl).to_dict()}
                )

                self._detect_stall(dl, progress)
            except (NodeError, OSError):
                # Preserve the request and its last verified facts while a node is away.
                continue

    def _detect_stall(self, dl, progress: float) -> None:
        now = time.time()
        last_progress, last_change = self._progress_seen.get(dl.id, (None, now))
        if last_progress is None or progress > last_progress + 1e-4:
            self._progress_seen[dl.id] = (progress, now)
            if dl.id in self._stall_flagged:
                job = self.store.get_job(dl.metadata.get("job_id", ""))
                if job:
                    # History alone must not introduce an extra reasoning wake.
                    self.storage.operations.job(
                        "download_recovered", job, subject=dl.id
                    )
            self._stall_flagged.discard(dl.id)
            return
        if now - last_change >= STALL_AFTER and dl.id not in self._stall_flagged:
            self._stall_flagged.add(dl.id)
            asyncio.create_task(
                self.emit(
                    Event(
                        kind="download_stalled",
                        job_id=dl.metadata.get("job_id", ""),
                        download_id=dl.id,
                        payload={
                            "description": f'"{dl.name}" has made no progress for '
                            f"{int((now - last_change) / 60)} minutes "
                            f"(stuck at {progress * 100:.0f}%). Your call: "
                            "wait, or kill it and take the runner-up.",
                            "download_id": dl.id,
                        },
                    )
                )
            )

    async def _recover_client(self, config, manager: TorrentManager) -> bool:
        """Start a configured local app at most once per cooldown window."""
        now = time.time()
        if now < self._client_recovery_after:
            return False
        self._client_recovery_after = now + CLIENT_RECOVERY_COOLDOWN

        started, message = await start_configured_client(config)
        if started or message != self._client_recovery_message:
            event = await self.storage.add_activity(
                "client_recovery",
                message,
                detail=(
                    f"Configured client: {config.type.value} at "
                    f"{config.host}:{config.port}"
                ),
                level="info" if started else "warning",
            )
            await self.broadcast({"type": "activity", "data": event.to_dict()})
        self._client_recovery_message = message
        if not started:
            return False

        for _ in range(8):
            await asyncio.sleep(1)
            if await manager.connect():
                event = await self.storage.add_activity(
                    "client_recovery",
                    "The download app is ready again.",
                    detail=f"Connected to {config.type.value} at {config.host}:{config.port}.",
                    level="success",
                )
                await self.broadcast({"type": "activity", "data": event.to_dict()})
                return True

        event = await self.storage.add_activity(
            "client_recovery",
            "The download app opened, but Sparrow still cannot connect to it.",
            detail=(
                "Enable its remote/web interface and confirm the configured address: "
                f"{config.host}:{config.port}."
            ),
            level="warning",
        )
        await self.broadcast({"type": "activity", "data": event.to_dict()})
        return False

    async def _client_transitions(self, up: bool) -> None:
        prev, self._client_up = self._client_up, up
        if prev is None or prev == up:
            return
        kind = "client_recovered" if up else "client_down"
        desc = (
            "The torrent client is reachable again — anything you were "
            "waiting to add can go in now."
            if up
            else "The torrent client just became unreachable. This is plumbing, "
            "not a torrent problem — explain the situation to the user in "
            "the journal in plain words."
        )
        for job in self.store.get_jobs(JobStatus.ACTIVE):
            has_active_dl = any(
                d.metadata.get("job_id") == job.id
                and d.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING)
                for d in self.storage.get_all_downloads()
            )
            if has_active_dl or up:
                await self.emit(
                    Event(kind=kind, job_id=job.id, payload={"description": desc})
                )
