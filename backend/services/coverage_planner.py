"""
Coverage planner — given "I want these episodes" and a messy pile of parsed
torrent candidates (season packs, partial packs, multi-episode files, singles),
compute the best set of torrents to download.

This is a greedy weighted set cover:
  - every candidate gets a base score (title match, quality, seeders, size
    sanity, source) — deterministic and cheap
  - we repeatedly pick the candidate with the best (score x new-coverage)
    until nothing useful remains
  - season packs naturally win when they're good; singles fill the gaps
  - a small bonus keeps release groups consistent across a season

Everything is explainable: the plan carries a plain-language reason per pick
and an overall summary a non-technical user can read.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from ..models import SearchResult, quality_rank
from .release_parser import ParsedRelease

_STOPWORDS = {"the", "a", "an", "of", "and", "&", "in", "on", "to", "us", "uk"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if t and t not in _STOPWORDS}


def title_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if inter == len(ta) or inter == len(tb):  # containment
        return 1.0
    return inter / len(ta | tb)


# Sane size-per-episode bounds in GB by quality (loose on the high side).
_EP_SIZE_GB = {
    "2160p": (1.5, 15.0),
    "1080p": (0.35, 7.0),
    "720p": (0.15, 3.0),
    "480p": (0.08, 1.5),
    "unknown": (0.08, 15.0),
}
_MOVIE_SIZE_GB = {
    "2160p": (4.0, 90.0),
    "1080p": (0.9, 40.0),
    "720p": (0.5, 12.0),
    "480p": (0.3, 5.0),
    "unknown": (0.3, 90.0),
}


@dataclass
class PlanContext:
    title: str
    media_type: str                     # movie | tv
    year: Optional[int] = None
    imdb_id: Optional[str] = None
    preferred_quality: str = "1080p"
    min_quality: str = "any"            # "any" or a quality string
    prefer_smaller: bool = False
    episode_counts: dict = field(default_factory=dict)  # {season(int): count}
    exclude_hashes: set = field(default_factory=set)     # already tried/failed


@dataclass
class PlanPick:
    result: SearchResult
    parsed: ParsedRelease
    covers: list[list[int]]             # [season, episode] pairs newly covered
    score: float
    reason: str                          # plain language

    def to_dict(self) -> dict:
        return {
            "result": self.result.to_dict(),
            "parsed": self.parsed.to_dict(),
            "covers": self.covers,
            "score": round(self.score, 1),
            "reason": self.reason,
        }


@dataclass
class CoveragePlan:
    picks: list[PlanPick]
    uncovered: list[list[int]]          # [season, episode] pairs we can't get
    confidence: str                     # auto | review | none
    summary: str                        # plain language, user-facing

    def to_dict(self) -> dict:
        return {
            "picks": [p.to_dict() for p in self.picks],
            "uncovered": self.uncovered,
            "confidence": self.confidence,
            "summary": self.summary,
        }


def _score_candidate(res: SearchResult, parsed: ParsedRelease, ctx: PlanContext) -> tuple[float, str]:
    """Base desirability 0-100 (coverage handled separately). Returns (score, reject_reason)."""
    if parsed.risk_flags:
        return 0.0, f"looks like a bad copy ({parsed.risk_flags[0]})"
    if res.seeders <= 0:
        return 0.0, "nobody is sharing it right now"
    if res.info_hash.lower() in ctx.exclude_hashes:
        return 0.0, "already tried"

    sim = title_similarity(parsed.title or res.name, ctx.title)
    if ctx.imdb_id and res.imdb_id and res.imdb_id == ctx.imdb_id:
        sim = 1.0
    if sim < 0.45:
        return 0.0, "doesn't look like the right title"
    score = sim * 25.0

    pq, want = quality_rank(parsed.quality), quality_rank(ctx.preferred_quality)
    minq = quality_rank(ctx.min_quality)
    if minq and pq and pq < minq:
        return 0.0, "below the minimum quality"
    if pq == 0:
        score += 6.0
    elif pq == want:
        score += 22.0
    elif pq < want:
        score += max(4.0, 14.0 - 5.0 * (want - pq))
    else:
        score += 8.0 if ctx.prefer_smaller else 14.0

    if ctx.media_type == "movie" and ctx.year and parsed.year and abs(parsed.year - ctx.year) > 1:
        score -= 20.0

    score += min(15.0, math.log2(1 + res.seeders) * 3.0)

    source_pts = {"REMUX": 7, "BLURAY": 8, "WEB-DL": 8, "WEBRIP": 5, "HDTV": 3, "DVD": 2}
    score += source_pts.get(parsed.source, 2)
    if ctx.prefer_smaller and parsed.codec == "x265":
        score += 4.0

    # Size sanity per covered episode (or per movie)
    if res.size_bytes > 0:
        gb = res.size_bytes / 1e9
        q = parsed.quality if parsed.quality in _EP_SIZE_GB else "unknown"
        if ctx.media_type == "movie":
            lo, hi = _MOVIE_SIZE_GB[q]
            per = gb
        else:
            n = _covered_count(parsed, ctx)
            per = gb / max(1, n)
            lo, hi = _EP_SIZE_GB[q]
        if per < lo:
            score -= 25.0  # suspiciously small → fake/sample risk
        elif per > hi:
            score -= 5.0 if not ctx.prefer_smaller else 15.0

    return max(0.0, min(100.0, score)), ""


def _covered_count(parsed: ParsedRelease, ctx: PlanContext) -> int:
    total = 0
    for season, count in ctx.episode_counts.items():
        total += len(parsed.covered_episodes(int(season), int(count)))
    return max(total, len(parsed.episodes))


def _coverage_set(parsed: ParsedRelease, wanted: dict[int, set[int]], ctx: PlanContext) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for season, eps in wanted.items():
        count = int(ctx.episode_counts.get(season, max(eps) if eps else 0))
        covered = parsed.covered_episodes(season, count)
        out |= {(season, e) for e in (covered & eps)}
    return out


def _quality_label(q: str) -> str:
    return {"2160p": "4K", "1080p": "Full HD", "720p": "HD", "480p": "standard quality"}.get(q, "unknown quality")


def plan_movie(candidates: list[tuple[SearchResult, ParsedRelease]], ctx: PlanContext) -> CoveragePlan:
    scored = []
    for res, parsed in candidates:
        s, why = _score_candidate(res, parsed, ctx)
        if s > 0 and parsed.media_type != "tv":
            scored.append((s, res, parsed))
    scored.sort(key=lambda t: t[0], reverse=True)
    if not scored:
        return CoveragePlan([], [], "none", f"Couldn't find a good copy of {ctx.title} yet.")
    s, res, parsed = scored[0]
    conf = "auto" if s >= 62 else ("review" if s >= 45 else "none")
    reason = f"Best available copy in {_quality_label(parsed.quality)}"
    pick = PlanPick(res, parsed, [], s, reason)
    summary = f"Found {ctx.title} in {_quality_label(parsed.quality)}."
    if conf == "none":
        return CoveragePlan([], [], "none", f"Couldn't find a trustworthy copy of {ctx.title} yet.")
    return CoveragePlan([pick], [], conf, summary)


def plan_episodes(
    candidates: list[tuple[SearchResult, ParsedRelease]],
    wanted_episodes: dict[int, set[int]],
    ctx: PlanContext,
) -> CoveragePlan:
    """Greedy set cover over the wanted episode set."""
    remaining: set[tuple[int, int]] = {
        (s, e) for s, eps in wanted_episodes.items() for e in eps
    }
    if not remaining:
        return CoveragePlan([], [], "none", "Nothing to get — everything is already in the library.")

    pool = []
    for res, parsed in candidates:
        score, _ = _score_candidate(res, parsed, ctx)
        if score <= 0:
            continue
        cover = _coverage_set(parsed, wanted_episodes, ctx)
        if cover:
            pool.append({"res": res, "parsed": parsed, "score": score, "cover": cover})

    picks: list[PlanPick] = []
    plan_groups: set[str] = set()
    used_hashes: set[str] = set()

    while remaining and pool:
        best = None
        best_value = 0.0
        for cand in pool:
            if cand["res"].info_hash.lower() in used_hashes:
                continue
            new_cover = cand["cover"] & remaining
            if not new_cover:
                continue
            # value: base score weighted by how much new ground it covers.
            # Packs get economies of scale; group consistency gets a nudge.
            value = cand["score"] * (1.0 + 0.35 * math.log2(1 + len(new_cover)))
            g = cand["parsed"].group.lower()
            if g and plan_groups and g in plan_groups:
                value *= 1.08
            if best is None or value > best_value:
                best, best_value = cand, value
        if best is None:
            break

        new_cover = sorted(best["cover"] & remaining)
        remaining -= best["cover"]
        used_hashes.add(best["res"].info_hash.lower())
        if best["parsed"].group:
            plan_groups.add(best["parsed"].group.lower())

        n = len(new_cover)
        parsed = best["parsed"]
        if parsed.is_complete_series:
            what = "the complete series"
        elif parsed.is_season_pack and n > 3:
            seasons = sorted({s for s, _ in new_cover})
            what = f"all of season {seasons[0]}" if len(seasons) == 1 else f"seasons {seasons[0]}–{seasons[-1]}"
        elif n == 1:
            s, e = new_cover[0]
            what = f"episode {e} of season {s}"
        else:
            s = new_cover[0][0]
            what = f"{n} episodes of season {s}"
        reason = f"Covers {what} in {_quality_label(parsed.quality)}"
        picks.append(PlanPick(best["res"], parsed, [list(t) for t in new_cover], best["score"], reason))

    covered_n = sum(len(p.covers) for p in picks)
    total_n = covered_n + len(remaining)
    uncovered = sorted(remaining)

    if not picks:
        summary = f"No good downloads for {ctx.title} available right now — Sparrow will keep looking."
        return CoveragePlan([], [list(t) for t in uncovered], "none", summary)

    avg_score = sum(p.score for p in picks) / len(picks)
    conf = "auto" if avg_score >= 58 else ("review" if avg_score >= 42 else "none")

    qualities = {p.parsed.quality for p in picks if quality_rank(p.parsed.quality)}
    q_note = _quality_label(min(qualities, key=quality_rank)) if qualities else "unknown quality"
    if not uncovered:
        summary = f"Found everything for {ctx.title} ({covered_n} episode{'s' if covered_n != 1 else ''}, {q_note} or better) across {len(picks)} download{'s' if len(picks) != 1 else ''}."
    else:
        miss = ", ".join(f"S{s:02d}E{e:02d}" for s, e in uncovered[:6])
        more = f" and {len(uncovered) - 6} more" if len(uncovered) > 6 else ""
        summary = (
            f"Found {covered_n} of {total_n} episodes for {ctx.title}. "
            f"Still missing {miss}{more} — Sparrow will keep looking for those."
        )
    return CoveragePlan(picks, [list(t) for t in uncovered], conf, summary)
