"""
Release selection engine.

Scores torrent candidates with cheap deterministic checks first, and exposes a
compact decision shape that request processing and the UI can inspect.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from ..models import MediaType, Quality, SearchResult
from .search_engine import _detect_codec, _detect_quality, _detect_source, _expected_season_size_gb, _looks_like_season_pack


STOP_WORDS = {"the", "a", "an", "and", "of", "in", "on", "at", "to", "for"}
RISK_TERMS = {
    "cam": "camera source",
    "hdcam": "camera source",
    "ts": "telesync source",
    "telesync": "telesync source",
    "tc": "telecine source",
    "sample": "sample file",
    "trailer": "trailer",
}
QUALITY_RANK = {"480p": 1, "720p": 2, "unknown": 2, "any": 2, "1080p": 3, "2160p": 4}


@dataclass
class ReleaseContext:
    query: str
    title: str
    media_type: MediaType = MediaType.UNKNOWN
    quality: Quality = Quality.Q_1080P
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_count: int = 0
    year: Optional[int] = None
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    aliases: list[str] = field(default_factory=list)
    prefer_smaller: bool = False
    prefer_season_packs: bool = True
    season_pack_size_limit_gb: float = 0.0


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", text.lower())
        if t not in STOP_WORDS and not re.fullmatch(r"\d{3,4}p", t)
    }


def _clean_family(name: str) -> str:
    n = name.lower()
    n = re.sub(r"\bs\d{1,2}e\d{1,3}\b|\bs\d{1,2}\b|\bseason\s+\d+\b", " ", n)
    n = re.sub(r"\b(2160p|1080p|720p|480p|web.?dl|webrip|bluray|bdrip|hdtv|x26[45]|h26[45]|hevc|aac|ddp?5?\.?1)\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return " ".join(t for t in n.split() if t not in STOP_WORDS)[:80]


def _release_group(name: str) -> str:
    match = re.search(r"-([A-Za-z0-9]+)(?:\.[A-Za-z0-9]+)?$", name.strip())
    if match:
        return match.group(1).lower()
    return _clean_family(name)


def _season_in_name(name: str) -> Optional[int]:
    n = name.lower()
    match = re.search(r"\bs(\d{1,2})(?:e\d{1,3})?\b", n) or re.search(r"\bseason\s+(\d{1,2})\b", n)
    return int(match.group(1)) if match else None


def _episode_in_name(name: str) -> Optional[int]:
    n = name.lower()
    match = re.search(r"\bs\d{1,2}e(\d{1,3})\b", n) or re.search(r"\bepisode\s+(\d{1,3})\b", n)
    return int(match.group(1)) if match else None


def _release_scope(name: str, season: Optional[int]) -> dict:
    n = name.lower()
    detected_season = _season_in_name(name)
    detected_episode = _episode_in_name(name)
    season_range = re.search(r"\bs(\d{1,2})\s*(?:-|to|thru|through)\s*s?(\d{1,2})\b", n)
    if re.search(r"\b(complete series|complete collection|all seasons|series complete)\b", n) or season_range:
        if season_range:
            start, end = int(season_range.group(1)), int(season_range.group(2))
            label = f"Complete series S{start:02d}-S{end:02d}"
        else:
            label = "Complete series"
        return {
            "scope": "complete_series",
            "label": label,
            "season": None,
            "episode": None,
            "is_pack": True,
        }
    if detected_episode:
        return {
            "scope": "episode",
            "label": f"Season {detected_season or season} episode {detected_episode}",
            "season": detected_season or season,
            "episode": detected_episode,
            "is_pack": False,
        }
    if detected_season or _looks_like_season_pack(name, season):
        s = detected_season or season
        return {
            "scope": "season",
            "label": f"Season {s}" if s else "Season pack",
            "season": s,
            "episode": None,
            "is_pack": True,
        }
    return {
        "scope": "unknown",
        "label": "Release",
        "season": None,
        "episode": None,
        "is_pack": False,
    }


def _title_component(result: SearchResult, context: ReleaseContext) -> tuple[float, list[str]]:
    name_tokens = _tokens(result.name)
    candidates = [context.title or context.query, *context.aliases]
    best = 0.0
    matched_title = ""
    for candidate in candidates:
        wanted = _tokens(candidate)
        if not wanted:
            continue
        coverage = len(name_tokens & wanted) / len(wanted)
        noise = max(0, len(name_tokens - wanted) - 8) * 0.015
        score = max(0.0, min(25.0, coverage * 25.0 - noise))
        if score > best:
            best = score
            matched_title = candidate
    notes = [f"title matched '{matched_title}'"] if best >= 18 and matched_title else []
    return round(best, 2), notes


def _identity_component(result: SearchResult, context: ReleaseContext) -> tuple[float, list[str]]:
    if context.imdb_id and result.imdb_id:
        return (20.0, ["IMDb id matched"]) if result.imdb_id == context.imdb_id else (-20.0, ["IMDb id mismatch"])
    if context.year and context.media_type != MediaType.TV:
        years = {int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", result.name)}
        if context.year in years:
            return 12.0, ["year matched"]
        if years:
            return -8.0, [f"year mismatch: {sorted(years)[0]}"]
    return 5.0, []


def _season_episode_component(result: SearchResult, context: ReleaseContext) -> tuple[float, list[str]]:
    if context.media_type != MediaType.TV:
        return 10.0, []
    scope = _release_scope(result.name, context.season)
    season_seen = _season_in_name(result.name)
    episode_seen = _episode_in_name(result.name)
    notes: list[str] = []
    if not context.season and not context.episode:
        if scope["scope"] == "complete_series":
            return 24.0, ["complete series"]
        if scope["scope"] == "season":
            return 12.0, [scope["label"]]
        if scope["scope"] == "episode":
            return -8.0, ["single episode for full-show request"]
        return 4.0, []
    if context.season and season_seen and season_seen != context.season:
        return -25.0, [f"wrong season S{season_seen:02d}"]
    if context.episode:
        if episode_seen == context.episode:
            return 25.0, [f"episode E{context.episode:02d} matched"]
        return -18.0, ["episode number not matched"]
    if context.season:
        if _looks_like_season_pack(result.name, context.season):
            notes.append("season pack matched")
            return 25.0, notes
        if season_seen == context.season:
            return 13.0, [f"season S{context.season:02d} matched"]
    return 0.0, []


def _quality_component(result: SearchResult, quality: Quality) -> tuple[float, list[str]]:
    wanted = quality.value
    actual = _detect_quality(result.name)
    if wanted == "any":
        return 10.0, [actual]
    diff = abs(QUALITY_RANK.get(actual, 2) - QUALITY_RANK.get(wanted, 3))
    score = {0: 15.0, 1: 8.0, 2: 3.0}.get(diff, 0.0)
    return score, [f"{actual} quality"]


def _seed_component(result: SearchResult) -> float:
    if result.seeders <= 0:
        return 0.0
    return round(min(15.0, 3.0 + math.log10(result.seeders + 1) * 6.0), 2)


def _size_component(result: SearchResult, context: ReleaseContext) -> tuple[float, list[str]]:
    if result.size_bytes <= 0:
        return 0.0, ["unknown size"]
    size_gb = result.size_bytes / 1024 ** 3
    if context.media_type == MediaType.TV and context.episode_count and _looks_like_season_pack(result.name, context.season):
        low, high = _expected_season_size_gb(context.episode_count, context.quality, context.prefer_smaller)
        if context.season_pack_size_limit_gb > 0 and size_gb > context.season_pack_size_limit_gb:
            return -35.0, [f"{size_gb:.1f} GB exceeds configured pack limit"]
        if low <= size_gb <= high:
            return 15.0, [f"{size_gb:.1f} GB in expected range"]
        if size_gb > high:
            return -22.0, [f"{size_gb:.1f} GB looks too large"]
        return -14.0, [f"{size_gb:.1f} GB looks too small"]

    ranges = {
        "2160p": (4.0, 80.0),
        "1080p": (0.7, 28.0),
        "720p": (0.35, 12.0),
        "480p": (0.15, 5.0),
        "any": (0.15, 80.0),
    }
    low, high = ranges.get(context.quality.value, (0.2, 30.0))
    if low <= size_gb <= high:
        bonus = 12.0
        if context.prefer_smaller and size_gb <= max(low * 4, 8.0):
            bonus = 15.0
        return bonus, [f"{size_gb:.1f} GB"]
    if size_gb > high:
        return -14.0, [f"{size_gb:.1f} GB looks too large"]
    return -8.0, [f"{size_gb:.1f} GB looks too small"]


def score_candidate(result: SearchResult, context: ReleaseContext) -> dict:
    """Score one release candidate and return component-level rationale."""
    risks: list[str] = []
    notes: list[str] = []
    components: dict[str, float] = {}

    title_score, title_notes = _title_component(result, context)
    components["title_match"] = title_score
    notes.extend(title_notes)

    identity_score, identity_notes = _identity_component(result, context)
    components["identity_match"] = identity_score
    (risks if identity_score < 0 else notes).extend(identity_notes)

    se_score, se_notes = _season_episode_component(result, context)
    components["season_episode_match"] = se_score
    (risks if se_score < 0 else notes).extend(se_notes)

    quality_score, quality_notes = _quality_component(result, context.quality)
    components["quality_match"] = quality_score
    notes.extend(quality_notes)

    source = _detect_source(result.name)
    components["source_quality"] = {
        "REMUX": 10.0, "BLURAY": 9.0, "WEB-DL": 9.0, "WEBRIP": 7.0,
        "BDRIP": 6.0, "HDTV": 4.0, "HDRIP": 3.0, "DVDRIP": 2.0,
    }.get(source, 3.0)

    components["seed_health"] = _seed_component(result)

    size_score, size_notes = _size_component(result, context)
    components["size_sanity"] = size_score
    (risks if size_score < 0 else notes).extend(size_notes)

    codec = _detect_codec(result.name)
    components["codec_preference"] = 8.0 if context.prefer_smaller and codec == "x265" else 5.0 if codec in {"x264", "x265"} else 1.0

    lower_name = result.name.lower()
    risk_penalty = 0.0
    for term, reason in RISK_TERMS.items():
        if re.search(rf"\b{re.escape(term)}\b", lower_name):
            risks.append(reason)
            risk_penalty -= 25.0 if reason in {"camera source", "telesync source"} else 12.0
    components["risk_penalty"] = risk_penalty

    raw_total = round(sum(components.values()), 2)
    total = round(max(0.0, min(100.0, raw_total)), 2)
    band = confidence_band(total, None, risks)
    scope = _release_scope(result.name, context.season)
    facts = {
        "quality": _detect_quality(result.name),
        "source": source,
        "codec": codec,
        "size_gb": round(result.size_bytes / 1024 ** 3, 2) if result.size_bytes else 0,
        "season_pack": scope["is_pack"],
        "scope": scope["scope"],
        "label": scope["label"],
        "season": scope["season"],
        "episode": scope["episode"],
        "release_group": _release_group(result.name),
    }
    reason_bits = notes[:3] + [f"{result.seeders} seeders"]
    if risks:
        reason_bits.append(f"risks: {', '.join(risks[:2])}")
    return {
        "score": total,
        "raw_score": raw_total,
        "confidence": band,
        "components": components,
        "risks": risks,
        "facts": facts,
        "reason": ", ".join(reason_bits),
    }


def rank_candidates(results: list[SearchResult], context: ReleaseContext) -> list[tuple[SearchResult, dict]]:
    scored = [(result, score_candidate(result, context)) for result in results]
    return sorted(scored, key=lambda pair: pair[1]["score"], reverse=True)


def confidence_band(score: float, margin: Optional[float], risks: list[str]) -> str:
    severe = any("mismatch" in risk or "wrong season" in risk or "exceeds configured" in risk for risk in risks)
    if severe:
        return "review"
    if score >= 80:
        return "auto"
    if score >= 65 and margin is not None and margin >= 12:
        return "auto"
    if score >= 45:
        return "review"
    return "reject"


def _candidate_payload(ranked: list[tuple[SearchResult, dict]], limit: int = 6) -> list[dict]:
    return [{"result": result.to_dict(), "score": score} for result, score in ranked[:limit]]


def _finish_selected(strategy: str, selected, ranked: list[tuple[SearchResult, dict]], reason: str) -> dict:
    best_score = ranked[0][1] if ranked else None
    second_score = ranked[1][1]["score"] if len(ranked) > 1 else None
    margin = round(best_score["score"] - second_score, 2) if best_score and second_score is not None else None
    if best_score:
        best_score["confidence"] = confidence_band(best_score["score"], margin, best_score.get("risks", []))
    return {
        "strategy": strategy,
        "selected": selected,
        "score": best_score,
        "alternatives": _candidate_payload(ranked[1:] if strategy in {"movie", "season_pack"} else ranked),
        "confidence": best_score.get("confidence") if best_score else "reject",
        "margin": margin,
        "needs_ai_review": bool(best_score and best_score.get("confidence") == "review"),
        "selection_method": "deterministic_scoring",
        "reason": reason,
    }


def choose_movie_release(results: list[SearchResult], context: ReleaseContext) -> dict:
    """Choose a movie release from ranked candidates."""
    ranked = rank_candidates(results, context)
    if not ranked:
        return {
            "strategy": "movie",
            "selected": None,
            "score": None,
            "alternatives": [],
            "confidence": "reject",
            "needs_ai_review": False,
            "selection_method": "deterministic_scoring",
            "reason": "No movie candidates found.",
        }
    best = ranked[0]
    return _finish_selected("movie", best[0].to_dict(), ranked, f"Selected movie release: {best[1]['reason']}")


def choose_season_release(
    pack_results: list[SearchResult],
    episode_results: list[SearchResult],
    context: ReleaseContext,
) -> dict:
    """Prefer a good season pack, otherwise fall back to individual episodes."""
    pack_context = context
    ranked_packs = rank_candidates(pack_results, pack_context)

    episode_context = ReleaseContext(**{**context.__dict__, "episode_count": 0})
    ranked_episodes = rank_candidates(episode_results, episode_context)

    if not context.prefer_season_packs:
        selected = [result.to_dict() for result, _ in ranked_episodes[:context.episode_count or len(ranked_episodes)]]
        decision = _finish_selected("episodes", selected, ranked_episodes, "Season packs are disabled in preferences; using individual episodes.")
        decision["pack_alternatives"] = _candidate_payload(ranked_packs)
        return decision

    best_pack = ranked_packs[0] if ranked_packs else None
    if best_pack and best_pack[1]["facts"].get("season_pack") and best_pack[1]["confidence"] in {"auto", "review"}:
        if not (context.season_pack_size_limit_gb > 0 and best_pack[1]["facts"].get("size_gb", 0) > context.season_pack_size_limit_gb):
            return _finish_selected("season_pack", best_pack[0].to_dict(), ranked_packs, f"Season pack selected: {best_pack[1]['reason']}")

    selected = [result.to_dict() for result, _ in ranked_episodes[:context.episode_count or len(ranked_episodes)]]
    decision = _finish_selected("episodes", selected, ranked_episodes, "No acceptable season pack found; using individual episodes.")
    decision["pack_alternatives"] = _candidate_payload(ranked_packs)
    if best_pack:
        decision["rejected_pack_score"] = best_pack[1]
    return decision


def choose_episode_set(episode_candidates: dict[int, list[SearchResult]], context: ReleaseContext) -> dict:
    """Pick one candidate per episode, lightly preferring consistent release families."""
    family_counts: defaultdict[str, int] = defaultdict(int)
    scored_by_episode: dict[int, list[tuple[SearchResult, dict]]] = {}
    for ep, candidates in episode_candidates.items():
        ep_context = ReleaseContext(**{**context.__dict__, "episode": ep, "episode_count": 0})
        ranked = rank_candidates(candidates, ep_context)
        scored_by_episode[ep] = ranked
        for result, score in ranked[:3]:
            if score["confidence"] != "reject":
                family_counts[score["facts"]["release_group"]] += 1

    selected: list[dict] = []
    missing: list[int] = []
    for ep in sorted(episode_candidates):
        ranked = scored_by_episode.get(ep, [])
        best_pair = None
        best_total = -999.0
        for result, score in ranked[:5]:
            family_bonus = min(8, family_counts.get(score["facts"]["release_group"], 0) * 2)
            total = score["score"] + family_bonus
            if total > best_total and score["confidence"] != "reject":
                best_pair = (result, score)
                best_total = total
        if best_pair:
            selected.append(best_pair[0].to_dict())
        else:
            missing.append(ep)

    return {
        "selected": selected,
        "missing_episodes": missing,
        "episode_count": len(selected),
        "family_counts": dict(sorted(family_counts.items(), key=lambda item: item[1], reverse=True)[:5]),
    }
