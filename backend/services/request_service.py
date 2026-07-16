"""
Intent request processor.

Turns a consumer request ("show season 2") into tracked work:
resolve intent, evaluate season packs first, then queue the best pack or episodes.
"""
from __future__ import annotations

import re
import json
import asyncio
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

from ..models import (
    Download, DownloadStatus, MediaRequest, MediaType, Quality, RequestStatus,
    RequestStrategy, TorrentClientType,
)
from . import metadata_service as meta_svc
from .release_decision_engine import ReleaseContext, choose_episode_set, choose_movie_release, rank_candidates
from . import search_engine
from . import torrent_client as tc_svc


Broadcast = Callable[[dict], Awaitable[None]]


def parse_request_intent(query: str, default_quality: Quality = Quality.Q_1080P) -> dict:
    """Extract the simple consumer intent shape before metadata enrichment."""
    q = query.strip()
    season: Optional[int] = None
    episode: Optional[int] = None
    quality = default_quality.value

    q_match = re.search(r'\b(2160p|4k|1080p|720p|480p)\b', q, re.IGNORECASE)
    if q_match:
        quality = "2160p" if q_match.group(1).lower() == "4k" else q_match.group(1).lower()

    s_match = re.search(r'\b(?:season|s)\s*(\d{1,2})\b', q, re.IGNORECASE)
    e_match = re.search(r'\b(?:episode|e)\s*(\d{1,3})\b', q, re.IGNORECASE)
    if s_match:
        season = int(s_match.group(1))
    if e_match:
        episode = int(e_match.group(1))

    title = re.sub(r'\b(2160p|4k|1080p|720p|480p)\b', '', q, flags=re.IGNORECASE)
    title = re.sub(r'\bseason\s*\d{1,2}\b|\bs\d{1,2}\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\bepisode\s*\d{1,3}\b|\be\d{1,3}\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s+', ' ', title).strip(" -")

    return {
        "query": q,
        "title": title or q,
        "season": season,
        "episode": episode,
        "quality": quality,
        "media_type": MediaType.TV if season or episode else MediaType.UNKNOWN,
    }


async def resolve_request_intent(request: MediaRequest, config) -> dict:
    """Resolve title/media type using TMDB when possible, falling back to parsed text."""
    try:
        default_quality = Quality(request.quality)
    except Exception:
        default_quality = config.quality_preference
    parsed = parse_request_intent(request.query, default_quality)
    resolved = dict(parsed)
    if not config.tmdb_api_key:
        return resolved

    suggestions = await meta_svc.tmdb_quick_suggest(parsed["title"], config.tmdb_api_key)
    if not suggestions:
        return resolved

    best = suggestions[0]
    resolved["title"] = best.get("title") or parsed["title"]
    resolved["tmdb_id"] = best.get("tmdb_id")
    try:
        resolved["year"] = int(best.get("year") or 0) or None
    except Exception:
        resolved["year"] = None
    resolved["media_type"] = MediaType(best.get("media_type", "unknown"))

    if resolved["media_type"] == MediaType.TV:
        seasons = await meta_svc.tmdb_tv_seasons(best["tmdb_id"], config.tmdb_api_key)
        resolved["seasons"] = seasons
        if parsed["season"]:
            for season_data in seasons:
                if season_data.get("season_number") == parsed["season"]:
                    resolved["episode_count"] = season_data.get("episode_count") or 0
                    break
    return resolved


async def agent_review_season_decision(query: str, decision: dict, api_key: str) -> str:
    """Cheap optional reviewer for ambiguous pack-vs-episode choices."""
    if not api_key:
        return ""
    score = decision.get("score") or {}
    if isinstance(score, dict):
        pack_score = float(score.get("score") or 0)
        if pack_score <= 0 or pack_score >= 48:
            return ""
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        msg = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": f"""You are Sparrow's release-selection reviewer.
User request: {query}
Deterministic decision: {decision}

In one short sentence, say whether the decision is sensible for a consumer and why. Do not add caveats."""}],
        )
        return "".join(getattr(block, "text", "") for block in msg.content).strip()
    except Exception:
        return ""


async def agent_review_release_decision(query: str, decision: dict, api_key: str) -> dict:
    """Structured low-token AI review for ambiguous deterministic choices."""
    if not api_key or not decision.get("needs_ai_review"):
        return {}
    candidates = []
    selected = decision.get("selected")
    if isinstance(selected, dict):
        candidates.append({
            "role": "selected",
            "name": selected.get("name"),
            "seeders": selected.get("seeders"),
            "size_human": selected.get("size_human"),
            "score": decision.get("score"),
        })
    for alt in (decision.get("alternatives") or [])[:4]:
        result = alt.get("result", {})
        candidates.append({
            "role": "alternative",
            "name": result.get("name"),
            "seeders": result.get("seeders"),
            "size_human": result.get("size_human"),
            "score": alt.get("score"),
        })
    payload = {
        "request": query,
        "strategy": decision.get("strategy"),
        "confidence": decision.get("confidence"),
        "margin": decision.get("margin"),
        "reason": decision.get("reason"),
        "candidates": candidates[:5],
    }
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        msg = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=180,
            messages=[{"role": "user", "content": f"""Review this torrent release decision for a consumer media request.
Return JSON only with keys: decision (accept|fallback|reject|ask_user), confidence (0-100), reason (short).
Do not mention policy, legality, or caveats.

{json.dumps(payload, separators=(',', ':'))}"""}],
        )
        text = "".join(getattr(block, "text", "") for block in msg.content).strip()
        return json.loads(text)
    except Exception:
        return {}


async def preview_request_plan(
    query: str,
    quality_value: str,
    config,
    data_dir: str,
    max_episode_checks: int = 3,
) -> dict:
    """Resolve and compare candidate strategies without queueing a torrent."""
    request = MediaRequest(id="preview", query=query, quality=quality_value or config.quality_preference.value)
    resolved = await resolve_request_intent(request, config)
    quality = Quality(resolved.get("quality") or config.quality_preference.value)
    media_type = resolved.get("media_type", MediaType.UNKNOWN)
    if isinstance(media_type, str):
        media_type = MediaType(media_type)
    title = resolved.get("title") or query
    season = resolved.get("season")
    episode_count = int(resolved.get("episode_count") or 0)
    year = resolved.get("year")
    tmdb_id = resolved.get("tmdb_id")
    selection_context = ReleaseContext(
        query=query,
        title=title,
        media_type=media_type,
        quality=quality,
        season=season,
        episode_count=episode_count,
        year=year,
        tmdb_id=tmdb_id,
        prefer_smaller=config.prefer_smaller_files,
        prefer_season_packs=config.prefer_season_packs,
        season_pack_size_limit_gb=config.season_pack_size_limit_gb,
    )

    plan: dict = {
        "query": query,
        "resolved": {
            "title": title,
            "media_type": media_type.value,
            "season": season,
            "episode_count": episode_count or None,
            "quality": quality.value,
            "tmdb_id": tmdb_id,
            "year": year,
        },
        "strategy": "unknown",
        "decision": None,
        "pack_candidates": [],
        "episode_candidates": [],
        "movie_candidates": [],
        "summary": "",
    }

    if media_type == MediaType.TV and not season:
        seasons_meta = [
            s for s in (resolved.get("seasons") or [])
            if int(s.get("season_number") or 0) > 0 and int(s.get("episode_count") or 0) > 0
        ]
        search_batches = await asyncio.gather(
            search_engine.search(
                title,
                media_type=MediaType.TV,
                quality=quality,
                limit=20,
                data_dir=data_dir,
                tmdb_api_key=config.tmdb_api_key,
                anthropic_api_key=config.anthropic_api_key,
            ),
            search_engine.search(
                f"{title} complete series",
                media_type=MediaType.TV,
                quality=quality,
                limit=12,
                data_dir=data_dir,
                tmdb_api_key=config.tmdb_api_key,
                anthropic_api_key="",
            ),
            search_engine.search(
                f"{title} complete {quality.value}",
                media_type=MediaType.TV,
                quality=quality,
                limit=12,
                data_dir=data_dir,
                tmdb_api_key=config.tmdb_api_key,
                anthropic_api_key="",
            ),
            return_exceptions=True,
        )
        by_hash = {}
        for batch in search_batches:
            if isinstance(batch, Exception):
                continue
            for result in batch:
                by_hash[result.info_hash.lower()] = result
        results = list(by_hash.values())
        ranked = rank_candidates(results, selection_context)
        raw_payload = [{"result": r.to_dict(), "score": s} for r, s in ranked[:24]]
        complete = [item for item in raw_payload if (item["score"].get("facts") or {}).get("scope") == "complete_series"]
        total_episodes = sum(int(s.get("episode_count") or 0) for s in seasons_meta)
        for item in complete:
            score = dict(item["score"])
            score["facts"] = {
                **(score.get("facts") or {}),
                "delivery": "complete_series",
                "episode_count": total_episodes or None,
                "coverage": total_episodes or None,
            }
            item["score"] = score
            item["results"] = [item["result"]]
            item["option"] = {
                "kind": "complete_series",
                "label": (score.get("facts") or {}).get("label") or "Complete series",
                "coverage": total_episodes or None,
                "episode_count": total_episodes or None,
            }
        season_options: list[dict] = []

        async def build_season_option(season_data: dict) -> Optional[dict]:
            season_num = int(season_data.get("season_number") or 0)
            season_episodes = int(season_data.get("episode_count") or 0)
            if season_num <= 0 or season_episodes <= 0:
                return None
            season_context = ReleaseContext(**{
                **selection_context.__dict__,
                "season": season_num,
                "episode_count": season_episodes,
            })
            packs = await search_engine.search_season_pack_torrents(
                title,
                season_num,
                quality,
                data_dir=data_dir,
                prefer_smaller=config.prefer_smaller_files,
                limit=6,
            )
            ranked_packs = rank_candidates(packs, season_context)
            ranked_packs = [
                pair for pair in ranked_packs
                if ((pair[1].get("facts") or {}).get("scope") != "complete_series")
            ]
            if ranked_packs:
                result, score = ranked_packs[0]
                score = dict(score)
                score["facts"] = {
                    **(score.get("facts") or {}),
                    "label": f"Season {season_num} complete",
                    "scope": "season",
                    "delivery": "season_pack",
                    "season": season_num,
                    "episode_count": season_episodes,
                    "coverage": season_episodes,
                }
                return {
                    "result": result.to_dict(),
                    "results": [result.to_dict()],
                    "score": score,
                    "option": {
                        "kind": "season_pack",
                        "label": f"Season {season_num} complete",
                        "season": season_num,
                        "episode_count": season_episodes,
                        "coverage": season_episodes,
                    },
                }

            episode_candidate_lists = await asyncio.gather(*[
                search_engine.search_episode_torrents(title, season_num, ep, quality, data_dir=data_dir, limit=3)
                for ep in range(1, season_episodes + 1)
            ], return_exceptions=True)
            by_episode = {
                ep: candidates
                for ep, candidates in enumerate(episode_candidate_lists, start=1)
                if not isinstance(candidates, Exception) and candidates
            }
            if not by_episode:
                return {
                    "result": {},
                    "results": [],
                    "score": {
                        "score": 0,
                        "confidence": "reject",
                        "facts": {
                            "label": f"Season {season_num}",
                            "scope": "season",
                            "delivery": "missing",
                            "season": season_num,
                            "episode_count": season_episodes,
                            "coverage": 0,
                        },
                        "components": {},
                        "risks": ["no season pack or episodes found"],
                    },
                    "option": {
                        "kind": "missing",
                        "label": f"Season {season_num}",
                        "season": season_num,
                        "episode_count": season_episodes,
                        "coverage": 0,
                    },
                }
            episode_plan = choose_episode_set(by_episode, season_context)
            selected_eps = episode_plan.get("selected") or []
            coverage = len(selected_eps)
            total_size = sum(int(item.get("size_bytes") or 0) for item in selected_eps)
            total_seeders = sum(int(item.get("seeders") or 0) for item in selected_eps)
            score_value = round(min(92, 48 + (coverage / season_episodes) * 34 + min(10, total_seeders / max(1, coverage) / 10)), 2)
            return {
                "result": selected_eps[0] if selected_eps else {},
                "results": selected_eps,
                "score": {
                    "score": score_value,
                    "confidence": "auto" if coverage == season_episodes else "review",
                    "components": {"coverage": coverage, "episodes": season_episodes},
                    "risks": [] if coverage == season_episodes else [f"missing {season_episodes - coverage} episodes"],
                    "facts": {
                        "label": f"Season {season_num} complete" if coverage == season_episodes else f"Season {season_num} partial",
                        "scope": "season",
                        "delivery": "episode_set",
                        "season": season_num,
                        "episode_count": season_episodes,
                        "coverage": coverage,
                        "quality": quality.value,
                        "size_gb": round(total_size / 1024 ** 3, 2) if total_size else 0,
                        "seeders": round(total_seeders / max(1, coverage)),
                    },
                    "reason": f"Stitched {coverage}/{season_episodes} episodes from individual releases.",
                },
                "option": {
                    "kind": "episode_set",
                    "label": f"Season {season_num} complete" if coverage == season_episodes else f"Season {season_num} partial",
                    "season": season_num,
                    "episode_count": season_episodes,
                    "coverage": coverage,
                },
            }

        season_tasks = [build_season_option(s) for s in seasons_meta[:12]]
        built_seasons = await asyncio.gather(*season_tasks, return_exceptions=True) if season_tasks else []
        for option in built_seasons:
            if isinstance(option, Exception) or not option:
                continue
            season_options.append(option)

        if not season_options:
            best_by_season: dict[int, dict] = {}
            for item in raw_payload:
                facts = item["score"].get("facts") or {}
                if facts.get("scope") != "season" or not facts.get("season"):
                    continue
                season_num = int(facts["season"])
                existing = best_by_season.get(season_num)
                if not existing or float(item["score"].get("score") or 0) > float(existing["score"].get("score") or 0):
                    best_by_season[season_num] = item
            season_options = [best_by_season[num] for num in sorted(best_by_season)]

        candidate_payload = complete[:2] + season_options[:12]
        preferred = complete[0] if complete else (candidate_payload[0] if candidate_payload else None)
        decision = {
            "strategy": "series",
            "selected": preferred["result"] if preferred else None,
            "score": preferred["score"] if preferred else None,
            "alternatives": candidate_payload[1:],
            "confidence": (preferred["score"].get("confidence") if preferred else "reject"),
            "needs_ai_review": False,
            "selection_method": "deterministic_scoring",
            "reason": (
                "Complete-series release available; selected as the default."
                if complete else
                "No complete-series release found; showing the strongest season-level releases."
                if season_options else
                "No clear series releases found."
            ),
        }
        plan.update({
            "strategy": "series",
            "decision": decision,
            "series_candidates": candidate_payload,
            "movie_candidates": candidate_payload,
            "summary": decision["reason"],
        })
    elif media_type == MediaType.TV and season:
        packs = await search_engine.search_season_pack_torrents(
            title,
            season,
            quality,
            data_dir=data_dir,
            prefer_smaller=config.prefer_smaller_files,
        )
        episode_results = []
        checks = min(episode_count or max_episode_checks, max_episode_checks)
        for ep in range(1, checks + 1):
            found = await search_engine.search_episode_torrent(title, season, ep, quality, data_dir=data_dir)
            if found:
                episode_results.append(found)
        decision = search_engine.choose_season_strategy(
            packs, episode_results, quality, query=query, title=title, media_type=media_type,
            season=season, episode_count=episode_count, prefer_smaller=config.prefer_smaller_files,
            prefer_season_packs=config.prefer_season_packs,
            season_pack_size_limit_gb=config.season_pack_size_limit_gb, year=year, tmdb_id=tmdb_id,
        )
        agent_review = await agent_review_release_decision(query, decision, config.anthropic_api_key)
        if agent_review:
            decision["agent_review"] = agent_review
        plan.update({
            "strategy": decision["strategy"],
            "decision": decision,
            "pack_candidates": [
                {"result": r.to_dict(), "score": s}
                for r, s in rank_candidates(packs, selection_context)[:5]
            ],
            "episode_candidates": [
                {"result": r.to_dict(), "score": s}
                for r, s in rank_candidates(episode_results, ReleaseContext(**{**selection_context.__dict__, "episode_count": 0}))[:5]
            ],
            "summary": decision.get("reason", ""),
        })
        if episode_count and checks < episode_count:
            plan["summary"] += f" Episode fallback preview sampled {checks} of {episode_count} episodes."
    else:
        results = await search_engine.search(
            title,
            media_type=MediaType.MOVIE if media_type != MediaType.TV else media_type,
            quality=quality,
            limit=8,
            data_dir=data_dir,
            tmdb_api_key=config.tmdb_api_key,
            anthropic_api_key=config.anthropic_api_key,
        )
        decision = choose_movie_release(results, selection_context)
        agent_review = await agent_review_release_decision(query, decision, config.anthropic_api_key)
        if agent_review:
            decision["agent_review"] = agent_review
        ranked = rank_candidates(results, selection_context)
        plan.update({
            "strategy": "movie",
            "decision": decision,
            "movie_candidates": [{"result": r.to_dict(), "score": s} for r, s in ranked[:5]],
            "summary": decision.get("reason") or ("No movie candidates found." if not ranked else ""),
        })
    return plan


async def _queue_download(storage, config, result: dict, media_type: MediaType, tmdb_id: Optional[int]) -> Download:
    manager = tc_svc.TorrentManager(config.torrent_client)
    torrent_hash = await manager.add_magnet(result["magnet_url"], config.staging_dir)
    dl = Download(
        id=str(uuid.uuid4()),
        name=result["name"],
        magnet_url=result["magnet_url"],
        media_type=media_type,
        status=DownloadStatus.QUEUED,
        torrent_hash=torrent_hash or result.get("info_hash", ""),
        tmdb_id=tmdb_id,
    )
    await storage.add_download(dl)
    return dl


async def process_request(storage, request_id: str, data_dir: str, broadcast: Broadcast) -> None:
    """Process one user request end to end enough to queue torrent work."""
    req = storage.get_request(request_id)
    if not req:
        return

    config = storage.get_config()
    try:
        await storage.update_request(request_id, status=RequestStatus.RESOLVING)
        await broadcast({"type": "request_update", "data": storage.get_request(request_id).to_dict()})

        resolved = await resolve_request_intent(req, config)
        quality = Quality(resolved.get("quality") or config.quality_preference.value)
        media_type = resolved.get("media_type", MediaType.UNKNOWN)
        if isinstance(media_type, str):
            media_type = MediaType(media_type)
        title = resolved.get("title") or req.query
        season = resolved.get("season")
        episode_count = int(resolved.get("episode_count") or 0)
        tmdb_id = resolved.get("tmdb_id")
        year = resolved.get("year")
        selection_context = ReleaseContext(
            query=req.query,
            title=title,
            media_type=media_type,
            quality=quality,
            season=season,
            episode_count=episode_count,
            year=year,
            tmdb_id=tmdb_id,
            prefer_smaller=config.prefer_smaller_files,
            prefer_season_packs=config.prefer_season_packs,
            season_pack_size_limit_gb=config.season_pack_size_limit_gb,
        )

        await storage.update_request(
            request_id,
            status=RequestStatus.EVALUATING,
            title=title,
            media_type=media_type,
            season=season,
            episode_count=episode_count or None,
            quality=quality.value,
            tmdb_id=tmdb_id,
            progress_total=episode_count or 1,
        )
        await broadcast({"type": "request_update", "data": storage.get_request(request_id).to_dict()})

        if config.torrent_client.type == TorrentClientType.NONE:
            await storage.update_request(
                request_id,
                status=RequestStatus.BLOCKED,
                error_message="No torrent client configured. Connect qBittorrent or Transmission, then retry this request.",
            )
            await broadcast({"type": "request_update", "data": storage.get_request(request_id).to_dict()})
            return
        manager_info = await tc_svc.TorrentManager(config.torrent_client).get_info()
        if not manager_info.reachable:
            await storage.update_request(
                request_id,
                status=RequestStatus.BLOCKED,
                error_message=f"{config.torrent_client.type.value} is not reachable on {config.torrent_client.host}:{config.torrent_client.port}. Fix health, then retry this request.",
            )
            await broadcast({"type": "request_update", "data": storage.get_request(request_id).to_dict()})
            return
        if not config.staging_dir:
            await storage.update_request(
                request_id,
                status=RequestStatus.BLOCKED,
                error_message="No staging directory configured. Choose one in Settings, then retry this request.",
            )
            await broadcast({"type": "request_update", "data": storage.get_request(request_id).to_dict()})
            return
        if not Path(config.staging_dir).exists():
            await storage.update_request(
                request_id,
                status=RequestStatus.BLOCKED,
                error_message=f"Staging folder does not exist: {config.staging_dir}. Use health fix, then retry this request.",
            )
            await broadcast({"type": "request_update", "data": storage.get_request(request_id).to_dict()})
            return

        if media_type == MediaType.TV and season:
            packs = await search_engine.search_season_pack_torrents(
                title, season, quality, data_dir=data_dir, prefer_smaller=config.prefer_smaller_files
            )
            episode_results = []
            episode_candidates: dict[int, list] = {}
            if episode_count:
                for ep in range(1, episode_count + 1):
                    candidates = await search_engine.search_episode_torrents(title, season, ep, quality, data_dir=data_dir)
                    episode_candidates[ep] = candidates
                    if candidates:
                        episode_results.append(candidates[0])

            decision = search_engine.choose_season_strategy(
                packs, episode_results, quality, query=req.query, title=title, media_type=media_type,
                season=season, episode_count=episode_count, prefer_smaller=config.prefer_smaller_files,
                prefer_season_packs=config.prefer_season_packs,
                season_pack_size_limit_gb=config.season_pack_size_limit_gb, year=year, tmdb_id=tmdb_id,
            )
            if decision["strategy"] == "episodes" and episode_candidates:
                episode_plan = choose_episode_set(episode_candidates, selection_context)
                decision["selected"] = episode_plan["selected"]
                decision["missing_episodes"] = episode_plan["missing_episodes"]
                decision["episode_plan"] = episode_plan
            agent_review = await agent_review_release_decision(req.query, decision, config.anthropic_api_key)
            if agent_review:
                decision["agent_review"] = agent_review

            if decision["strategy"] == "season_pack" and decision.get("selected"):
                dl = await _queue_download(storage, config, decision["selected"], MediaType.TV, tmdb_id)
                await storage.link_request_download(request_id, dl.id)
                await storage.update_request(
                    request_id,
                    status=RequestStatus.DOWNLOADING,
                    strategy=RequestStrategy.SEASON_PACK,
                    progress_found=episode_count or 1,
                    decision_summary=decision["reason"],
                    evaluation=decision,
                )
                await broadcast({"type": "download_added", "data": dl.to_dict()})
            elif decision.get("selected"):
                queued = []
                for result in decision["selected"]:
                    dl = await _queue_download(storage, config, result, MediaType.TV, tmdb_id)
                    queued.append(dl)
                    await storage.link_request_download(request_id, dl.id)
                    await broadcast({"type": "download_added", "data": dl.to_dict()})
                missing = decision.get("missing_episodes") or ([ep for ep in range(1, episode_count + 1) if ep > len(queued)] if episode_count else [])
                await storage.update_request(
                    request_id,
                    status=RequestStatus.DOWNLOADING if not missing else RequestStatus.PARTIAL,
                    strategy=RequestStrategy.EPISODES,
                    progress_found=len(queued),
                    missing_episodes=missing,
                    decision_summary=decision["reason"],
                    evaluation=decision,
                )
            else:
                await storage.update_request(
                    request_id,
                    status=RequestStatus.FAILED,
                    strategy=RequestStrategy.EPISODES,
                    error_message="No acceptable season pack or episode torrents found.",
                    evaluation=decision,
                )
        else:
            results = await search_engine.search(
                title,
                media_type=MediaType.MOVIE if media_type != MediaType.TV else media_type,
                quality=quality,
                limit=8,
                data_dir=data_dir,
                tmdb_api_key=config.tmdb_api_key,
                anthropic_api_key=config.anthropic_api_key,
            )
            decision = choose_movie_release(results, selection_context)
            agent_review = await agent_review_release_decision(req.query, decision, config.anthropic_api_key)
            if agent_review:
                decision["agent_review"] = agent_review
            if not decision.get("selected"):
                await storage.update_request(request_id, status=RequestStatus.FAILED, error_message="No torrent results found.")
            else:
                dl = await _queue_download(storage, config, decision["selected"], MediaType.MOVIE, tmdb_id)
                await storage.link_request_download(request_id, dl.id)
                await storage.update_request(
                    request_id,
                    status=RequestStatus.DOWNLOADING,
                    strategy=RequestStrategy.MOVIE,
                    progress_found=1,
                    progress_total=1,
                    decision_summary=decision["reason"],
                    evaluation=decision,
                )
                await broadcast({"type": "download_added", "data": dl.to_dict()})

    except Exception as exc:
        await storage.update_request(request_id, status=RequestStatus.FAILED, error_message=str(exc))

    refreshed = storage.get_request(request_id)
    if refreshed:
        await broadcast({"type": "request_update", "data": refreshed.to_dict()})
