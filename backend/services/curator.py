"""
Curator — the autonomous librarian.

Every goal (MediaRequest) is a durable statement of intent: "I want this movie"
or "I want these episodes of this show, ideally at this quality". The curator
runs continuously and closes the gap between what the user wants and what is
actually on disk:

  tick →  for each active goal:
            what do I want?          (goal.wanted_episodes)
            what do I have?          (library episode inventory)
            what is on the way?      (linked active downloads, parsed)
            what is missing?         → search, plan coverage, queue torrents
            what is below par?       → hunt upgrades on a slower cadence

Design rules:
  - deterministic parsing + planning does the normal work (free)
  - Haiku parses weird release names and invents alternate queries (cheap)
  - Sonnet arbitrates only when a plan is genuinely ambiguous (rare)
  - every action is narrated to the activity feed in plain language
  - a failed/tried torrent is never grabbed again for the same goal
  - backoff between attempts so we never hammer indexers
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Awaitable, Callable, Optional

from ..models import (
    Download, DownloadStatus, MediaRequest, MediaType, Quality,
    RequestStatus, RequestStrategy, quality_rank,
)
from . import torrent_client as tc_svc
from . import metadata_service
from .search_engine import _query, _to_result
from .release_parser import parse_releases, ParsedRelease
from .coverage_planner import PlanContext, plan_episodes, plan_movie, CoveragePlan

log = logging.getLogger("sparrow.curator")

Broadcast = Callable[[dict], Awaitable[None]]

SONNET_MODEL = "claude-sonnet-4-6"

# Backoff schedule between search attempts for a goal (seconds).
BACKOFF_STEPS = [15 * 60, 60 * 60, 3 * 3600, 6 * 3600, 24 * 3600]
UPGRADE_INTERVAL = 24 * 3600      # how often to hunt better quality
ESCALATION_COOLDOWN = 6 * 3600    # min gap between Sonnet arbitrations per goal
ACTIVE_DL_STATUSES = {
    DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.SEEDING,
    DownloadStatus.COMPLETED, DownloadStatus.ORGANIZING,
}


class Curator:
    def __init__(self, storage, data_dir: str, broadcast: Broadcast):
        self.storage = storage
        self.data_dir = data_dir
        self.broadcast = broadcast
        self._running = False
        self._wake = asyncio.Event()

    # ─── lifecycle ────────────────────────────────────────────────────────

    async def run_forever(self, interval: float = 120.0) -> None:
        self._running = True
        log.info("curator loop started")
        while self._running:
            try:
                await self.tick()
            except Exception:
                log.exception("curator tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def stop(self) -> None:
        self._running = False
        self._wake.set()

    def poke(self) -> None:
        """Wake the loop immediately (goal created, download organized, …)."""
        self._wake.set()

    # ─── narration ────────────────────────────────────────────────────────

    async def say(self, kind: str, message: str, goal: Optional[MediaRequest] = None,
                  detail: str = "", level: str = "info") -> None:
        event = await self.storage.add_activity(
            kind, message, detail=detail,
            request_id=goal.id if goal else "",
            tmdb_id=goal.tmdb_id if goal else None,
            level=level,
        )
        try:
            await self.broadcast({"type": "activity", "data": event.to_dict()})
        except Exception:
            pass

    # ─── goal creation ────────────────────────────────────────────────────

    async def create_goal(
        self,
        query: str = "",
        tmdb_id: Optional[int] = None,
        media_type: MediaType = MediaType.UNKNOWN,
        title: str = "",
        seasons: Optional[list[int]] = None,
        episodes: Optional[dict] = None,   # {"2": [7, 9]} for specific episodes
        quality: str = "",
        min_quality: str = "any",
    ) -> MediaRequest:
        """Create a durable goal. Resolves title/season shape from TMDB."""
        config = self.storage.get_config()
        quality = quality or config.quality_preference.value
        year = None

        if tmdb_id and config.tmdb_api_key:
            if media_type == MediaType.TV:
                details = await metadata_service.get_tv_details(tmdb_id, config.tmdb_api_key)
                if details:
                    title = details.get("name") or title
                    year = int((details.get("first_air_date") or "0000")[:4] or 0) or None
            elif media_type == MediaType.MOVIE:
                details = await metadata_service.get_movie_details(tmdb_id, config.tmdb_api_key)
                if details:
                    title = details.get("title") or title
                    year = int((details.get("release_date") or "0000")[:4] or 0) or None

        wanted: dict[str, list[int]] = {}
        if media_type == MediaType.TV:
            season_list = await metadata_service.tmdb_tv_seasons(tmdb_id, config.tmdb_api_key) if tmdb_id else []
            counts = {s["season_number"]: s["episode_count"] for s in season_list}
            if episodes:
                wanted = {str(s): sorted(set(int(e) for e in eps)) for s, eps in episodes.items()}
            elif seasons:
                for s in seasons:
                    n = counts.get(int(s), 0)
                    wanted[str(s)] = list(range(1, n + 1)) if n else []
            else:  # whole show
                for s, n in counts.items():
                    wanted[str(s)] = list(range(1, n + 1)) if n else []

        goal = MediaRequest(
            id=str(uuid.uuid4()),
            query=query or title,
            status=RequestStatus.PENDING,
            strategy=RequestStrategy.MOVIE if media_type == MediaType.MOVIE else RequestStrategy.SERIES,
            title=title or query,
            media_type=media_type,
            season=int(seasons[0]) if seasons and len(seasons) == 1 else None,
            quality=quality,
            min_quality=min_quality,
            tmdb_id=tmdb_id,
            wanted_episodes=wanted,
            progress_total=sum(len(v) for v in wanted.values()) or 1,
            evaluation={"year": year} if year else {},
        )
        await self.storage.add_request(goal)
        await self.broadcast({"type": "request_added", "data": goal.to_dict()})
        n_eps = sum(len(v) for v in wanted.values())
        what = title or query
        if media_type == MediaType.TV and n_eps:
            scope = f"{n_eps} episodes" if len(wanted) > 1 else f"season {list(wanted)[0]} ({n_eps} episodes)"
            await self.say("searching", f"Looking for {what} — {scope}.", goal)
        else:
            await self.say("searching", f"Looking for {what}.", goal)
        self.poke()
        return goal

    # ─── inventory helpers ────────────────────────────────────────────────

    def _find_library_item(self, goal: MediaRequest):
        for item in self.storage.get_library():
            # TMDB movie and TV ids are separate namespaces — never cross-match
            if goal.media_type != MediaType.UNKNOWN and item.media_type != goal.media_type:
                continue
            if goal.tmdb_id and item.tmdb_id == goal.tmdb_id:
                return item
            if not goal.tmdb_id and item.title.lower() == (goal.title or "").lower():
                return item
        return None

    def _have_map(self, goal: MediaRequest) -> dict[int, dict[int, dict]]:
        """season -> episode -> file info, from the library inventory."""
        item = self._find_library_item(goal)
        if not item or not item.episodes:
            return {}
        return {
            int(s): {int(e): info for e, info in eps.items()}
            for s, eps in item.episodes.items()
        }

    async def _in_flight(self, goal: MediaRequest) -> set[tuple[int, int]]:
        """Episodes covered by this goal's still-active downloads."""
        covered: set[tuple[int, int]] = set()
        names = []
        counts = {int(s): len(v) for s, v in goal.wanted_episodes.items()}
        for dl_id in goal.download_ids:
            dl = self.storage.get_download(dl_id)
            if not dl or dl.status not in ACTIVE_DL_STATUSES:
                continue
            pre = dl.metadata.get("covers")
            if pre:
                covered |= {(int(s), int(e)) for s, e in pre}
            else:
                names.append(dl.name)
        if names:
            config = self.storage.get_config()
            parsed = await parse_releases(names, self.storage, config.anthropic_api_key)
            for p in parsed:
                for s, n in counts.items():
                    covered |= {(s, e) for e in p.covered_episodes(s, n)}
        return covered

    # ─── searching ────────────────────────────────────────────────────────

    def _build_queries(self, goal: MediaRequest, missing: dict[int, set[int]]) -> list[str]:
        title = goal.title or goal.query
        queries = [title]
        year = (goal.evaluation or {}).get("year")
        if goal.media_type == MediaType.MOVIE:
            if year:
                queries.insert(0, f"{title} {year}")
            return queries
        for s in sorted(missing):
            queries.append(f"{title} S{s:02d}")
            queries.append(f"{title} season {s}")
        flat = sorted((s, e) for s, eps in missing.items() for e in eps)
        # A few episode-specific probes: most useful when only gaps remain
        if len(flat) <= 8:
            for s, e in flat:
                queries.append(f"{title} S{s:02d}E{e:02d}")
        extra = (goal.evaluation or {}).get("alt_queries") or []
        queries.extend(extra)
        # de-dup, preserve order, cap
        seen, out = set(), []
        for q in queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                out.append(q)
        return out[:14]

    async def _search(self, queries: list[str]) -> list:
        results, seen = [], set()
        for q in queries:
            for raw in await _query(q):
                r = _to_result(raw)
                h = r.info_hash.lower()
                if h not in seen:
                    seen.add(h)
                    results.append(r)
            await asyncio.sleep(0.3)  # be polite to the indexer
        return results

    async def _alt_queries_via_haiku(self, goal: MediaRequest) -> list[str]:
        """One cheap call to invent alternate search phrasings; cached on the goal."""
        config = self.storage.get_config()
        if not config.anthropic_api_key:
            return []
        ev = goal.evaluation or {}
        if ev.get("alt_queries") is not None:
            return ev["alt_queries"]
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                tools=[{
                    "name": "suggest_queries",
                    "description": "Suggest alternate torrent search queries.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
                        "required": ["queries"],
                    },
                }],
                tool_choice={"type": "tool", "name": "suggest_queries"},
                messages=[{
                    "role": "user",
                    "content": (
                        f"Torrent searches for '{goal.title}' "
                        f"({goal.media_type.value}) found nothing. Suggest up to 4 "
                        "alternate search queries: alternative/original titles, "
                        "romanized titles for anime, common abbreviations, or "
                        "franchise names. Queries only, no quality tags."
                    ),
                }],
            )
            alt = []
            for block in resp.content:
                if block.type == "tool_use":
                    alt = [q for q in block.input.get("queries", []) if isinstance(q, str)][:4]
            ev["alt_queries"] = alt
            await self.storage.update_request(goal.id, evaluation=ev)
            return alt
        except Exception:
            return []

    # ─── Sonnet escalation ────────────────────────────────────────────────

    async def _arbitrate(self, goal: MediaRequest, plan: CoveragePlan) -> bool:
        """Ask Sonnet whether a 'review'-confidence plan should proceed.

        Returns True to proceed. Fails open only to 'wait' (False) — the goal
        just retries later, nothing is downloaded on a bad call.
        """
        config = self.storage.get_config()
        ev = goal.evaluation or {}
        last = ev.get("last_escalation", 0)
        if not config.anthropic_api_key or time.time() - last < ESCALATION_COOLDOWN:
            return False
        ev["last_escalation"] = time.time()
        await self.storage.update_request(goal.id, evaluation=ev)
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
            picks_desc = "\n".join(
                f"- {p.result.name} | {p.result.seeders} seeders | "
                f"{p.result.size_bytes / 1e9:.1f} GB | covers {len(p.covers) or 'full'} episodes | score {p.score:.0f}"
                for p in plan.picks
            )
            resp = await client.messages.create(
                model=SONNET_MODEL,
                max_tokens=400,
                tools=[{
                    "name": "verdict",
                    "description": "Decide whether to proceed with this download plan.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "proceed": {"type": "boolean"},
                            "reason": {"type": "string"},
                        },
                        "required": ["proceed", "reason"],
                    },
                }],
                tool_choice={"type": "tool", "name": "verdict"},
                messages=[{
                    "role": "user",
                    "content": (
                        f"User wants: {goal.title} ({goal.media_type.value}), "
                        f"preferred quality {goal.quality}.\n"
                        f"The automatic planner is unsure about this plan:\n{picks_desc}\n\n"
                        "Judge: do these releases plausibly belong to the right "
                        "title and look safe (sane sizes, no fakes)? If yes, proceed."
                    ),
                }],
            )
            for block in resp.content:
                if block.type == "tool_use":
                    proceed = bool(block.input.get("proceed"))
                    note = block.input.get("reason", "")
                    await self.say(
                        "found" if proceed else "waiting",
                        ("Double-checked the download plan and it looks right."
                         if proceed else "The available downloads looked doubtful, so Sparrow will wait for better options."),
                        goal, detail=note,
                    )
                    return proceed
        except Exception:
            log.exception("sonnet arbitration failed")
        return False

    # ─── queueing ─────────────────────────────────────────────────────────

    async def _queue_pick(self, goal: MediaRequest, pick) -> Optional[Download]:
        config = self.storage.get_config()
        try:
            manager = tc_svc.TorrentManager(config.torrent_client)
            torrent_hash = await manager.add_magnet(pick.result.magnet_url, config.staging_dir)
        except Exception as exc:
            goal.record_attempt(pick.result.info_hash, pick.result.name, "failed", str(exc))
            await self.storage.update_request(goal.id, attempts=goal.attempts)
            return None
        dl = Download(
            id=str(uuid.uuid4()),
            name=pick.result.name,
            magnet_url=pick.result.magnet_url,
            media_type=goal.media_type,
            status=DownloadStatus.QUEUED,
            torrent_hash=torrent_hash or pick.result.info_hash,
            tmdb_id=goal.tmdb_id,
            quality=pick.parsed.quality if pick.parsed.quality != "unknown" else "",
            metadata={
                "goal_id": goal.id,
                "covers": pick.covers,
                "parsed": pick.parsed.to_dict(),
                "reason": pick.reason,
            },
        )
        await self.storage.add_download(dl)
        await self.storage.link_request_download(goal.id, dl.id)
        goal.record_attempt(pick.result.info_hash, pick.result.name, "queued", pick.reason)
        await self.storage.update_request(goal.id, attempts=goal.attempts)
        await self.broadcast({"type": "download_added", "data": dl.to_dict()})
        return dl

    # ─── the core: process one goal ───────────────────────────────────────

    async def process_goal(self, goal_id: str) -> None:
        goal = self.storage.get_request(goal_id)
        if not goal or goal.paused:
            return
        config = self.storage.get_config()

        if goal.media_type == MediaType.MOVIE:
            await self._process_movie(goal, config)
        else:
            await self._process_tv(goal, config)

    async def _process_movie(self, goal: MediaRequest, config) -> None:
        item = self._find_library_item(goal)
        if item:
            if goal.status != RequestStatus.COMPLETE:
                await self._set_status(goal, RequestStatus.COMPLETE, progress_found=1)
                await self.say("organized", f"{goal.title} is in your library and ready to watch.", goal, level="success")
            return
        in_flight = [
            d for d in (self.storage.get_download(i) for i in goal.download_ids)
            if d and d.status in ACTIVE_DL_STATUSES
        ]
        if in_flight:
            await self._set_status(goal, RequestStatus.DOWNLOADING)
            return

        await self._set_status(goal, RequestStatus.SEARCHING)
        queries = self._build_queries(goal, {})
        results = await self._search(queries)
        if not results:
            alt = await self._alt_queries_via_haiku(goal)
            if alt:
                results = await self._search(alt)
        parsed = await parse_releases([r.name for r in results], self.storage, config.anthropic_api_key)
        ctx = PlanContext(
            title=goal.title, media_type="movie",
            year=(goal.evaluation or {}).get("year"),
            preferred_quality=goal.quality, min_quality=goal.min_quality,
            prefer_smaller=config.prefer_smaller_files,
            exclude_hashes=goal.tried_hashes(),
        )
        plan = plan_movie(list(zip(results, parsed)), ctx)
        await self._execute_plan(goal, plan)

    async def _process_tv(self, goal: MediaRequest, config) -> None:
        wanted: dict[int, set[int]] = {
            int(s): set(eps) for s, eps in (goal.wanted_episodes or {}).items() if eps
        }
        if not wanted:
            # Old-style or unresolved goal: nothing structured to chase
            return

        have = self._have_map(goal)
        have_set = {(s, e) for s, eps in have.items() for e in eps}
        flight = await self._in_flight(goal)
        wanted_set = {(s, e) for s, eps in wanted.items() for e in eps}

        missing_set = wanted_set - have_set - flight
        found = len(wanted_set & have_set)
        missing_eps = sorted({e for _, e in missing_set})
        if (goal.progress_found != found or goal.progress_total != len(wanted_set)
                or goal.missing_episodes != missing_eps):
            await self.storage.update_request(
                goal.id,
                progress_found=found,
                progress_total=len(wanted_set),
                missing_episodes=missing_eps,
            )

        if not missing_set:
            if flight:
                await self._set_status(goal, RequestStatus.DOWNLOADING)
                return
            # Everything on disk → complete; then consider upgrades
            if goal.status != RequestStatus.COMPLETE:
                await self._set_status(goal, RequestStatus.COMPLETE)
                await self.say(
                    "organized",
                    f"{goal.title} is complete — all {len(wanted_set)} episodes are in your library.",
                    goal, level="success",
                )
            if goal.upgrade:
                await self._hunt_upgrades(goal, config, wanted, have)
            return

        # Something is missing → search and plan
        missing: dict[int, set[int]] = {}
        for s, e in missing_set:
            missing.setdefault(s, set()).add(e)

        await self._set_status(goal, RequestStatus.SEARCHING)
        queries = self._build_queries(goal, missing)
        results = await self._search(queries)
        if not results:
            alt = await self._alt_queries_via_haiku(goal)
            if alt:
                results = await self._search(alt)

        parsed = await parse_releases([r.name for r in results], self.storage, config.anthropic_api_key)
        ctx = PlanContext(
            title=goal.title, media_type="tv",
            year=(goal.evaluation or {}).get("year"),
            preferred_quality=goal.quality, min_quality=goal.min_quality,
            prefer_smaller=config.prefer_smaller_files,
            episode_counts={int(s): len(eps) for s, eps in (goal.wanted_episodes or {}).items()},
            exclude_hashes=goal.tried_hashes(),
        )
        plan = plan_episodes(list(zip(results, parsed)), missing, ctx)
        await self._execute_plan(goal, plan)

    async def _hunt_upgrades(self, goal: MediaRequest, config, wanted, have) -> None:
        """Look for better-quality versions of below-preference episodes."""
        ev = goal.evaluation or {}
        if time.time() - ev.get("last_upgrade_hunt", 0) < UPGRADE_INTERVAL:
            return
        want_rank = quality_rank(goal.quality)
        below: dict[int, set[int]] = {}
        for s, eps in have.items():
            for e, info in eps.items():
                if (s, e) in {(s2, e2) for s2, eps2 in wanted.items() for e2 in eps2}:
                    if quality_rank(info.get("quality", "")) < want_rank:
                        below.setdefault(s, set()).add(e)
        ev["last_upgrade_hunt"] = time.time()
        await self.storage.update_request(goal.id, evaluation=ev)
        if not below:
            return
        n = sum(len(v) for v in below.values())
        await self.say("searching", f"Quietly checking for better quality versions of {n} episode{'s' if n != 1 else ''} of {goal.title}.", goal)
        queries = self._build_queries(goal, below)
        results = await self._search(queries)
        parsed = await parse_releases([r.name for r in results], self.storage, config.anthropic_api_key)
        ctx = PlanContext(
            title=goal.title, media_type="tv",
            year=(goal.evaluation or {}).get("year"),
            preferred_quality=goal.quality,
            min_quality=goal.quality,   # upgrades must hit the preferred tier
            prefer_smaller=config.prefer_smaller_files,
            episode_counts={int(s): len(eps) for s, eps in (goal.wanted_episodes or {}).items()},
            exclude_hashes=goal.tried_hashes(),
        )
        plan = plan_episodes(list(zip(results, parsed)), below, ctx)
        if plan.confidence == "auto" and plan.picks:
            for pick in plan.picks:
                dl = await self._queue_pick(goal, pick)
                if dl:
                    dl.metadata["upgrade"] = True
                    await self.storage.update_download(dl.id, metadata=dl.metadata)
            await self.say("upgraded", f"Found better quality versions for {goal.title} — upgrading in the background.", goal, level="success")

    async def _execute_plan(self, goal: MediaRequest, plan: CoveragePlan) -> None:
        if plan.confidence == "review" and plan.picks:
            if not await self._arbitrate(goal, plan):
                await self._defer(goal, note=plan.summary)
                return
        elif plan.confidence != "auto" or not plan.picks:
            await self._defer(goal, note=plan.summary)
            return

        queued = 0
        for pick in plan.picks:
            dl = await self._queue_pick(goal, pick)
            if dl:
                queued += 1
        if queued:
            await self.storage.update_request(
                goal.id,
                status=RequestStatus.DOWNLOADING,
                strategy=self._strategy_for(plan),
                decision_summary=plan.summary,
                check_interval=0.0,
                next_check_at=time.time() + 10 * 60,
                error_message="",
            )
            await self.say("downloading", plan.summary, goal, level="success")
            await self.broadcast({"type": "request_update", "data": self.storage.get_request(goal.id).to_dict()})
        else:
            await self._defer(goal, note="Couldn't start the downloads — will try again soon.")

    def _strategy_for(self, plan: CoveragePlan) -> RequestStrategy:
        if any(p.parsed.is_complete_series for p in plan.picks):
            return RequestStrategy.SERIES
        if any(p.parsed.is_season_pack for p in plan.picks):
            return RequestStrategy.SEASON_PACK
        if plan.picks and plan.picks[0].parsed.media_type == "movie":
            return RequestStrategy.MOVIE
        return RequestStrategy.EPISODES

    async def _defer(self, goal: MediaRequest, note: str = "") -> None:
        """Nothing (good) available now — back off and try again later."""
        current = goal.check_interval or 0
        try:
            idx = BACKOFF_STEPS.index(int(current))
            nxt = BACKOFF_STEPS[min(idx + 1, len(BACKOFF_STEPS) - 1)]
        except ValueError:
            nxt = BACKOFF_STEPS[0]
        have_any = goal.progress_found > 0
        status = RequestStatus.PARTIAL if have_any else RequestStatus.WAITING
        await self.storage.update_request(
            goal.id, status=status, check_interval=float(nxt),
            next_check_at=time.time() + nxt,
            decision_summary=note or goal.decision_summary,
        )
        await self.broadcast({"type": "request_update", "data": self.storage.get_request(goal.id).to_dict()})
        hours = nxt / 3600
        when = f"{int(nxt / 60)} minutes" if nxt < 3600 else (f"{int(hours)} hour" + ("s" if hours >= 2 else ""))
        if note:
            await self.say("waiting", f"{note} Next check in about {when}.", goal)

    async def _set_status(self, goal: MediaRequest, status: RequestStatus, **kw) -> None:
        if goal.status != status or kw:
            await self.storage.update_request(goal.id, status=status, **kw)
            await self.broadcast({"type": "request_update", "data": self.storage.get_request(goal.id).to_dict()})

    # ─── the tick ─────────────────────────────────────────────────────────

    async def tick(self) -> None:
        now = time.time()
        for goal in self.storage.get_requests():
            if goal.paused:
                continue
            if goal.status == RequestStatus.FAILED:
                continue
            if goal.next_check_at > now:
                continue
            # Terminal-complete movies never need re-checking
            if goal.media_type == MediaType.MOVIE and goal.status == RequestStatus.COMPLETE:
                continue
            # Complete TV goals only need the (rate-limited) upgrade pass
            try:
                await self.process_goal(goal.id)
            except Exception:
                log.exception("processing goal %s failed", goal.id)
            # Small pause between goals to smooth out indexer traffic
            await asyncio.sleep(1.0)
