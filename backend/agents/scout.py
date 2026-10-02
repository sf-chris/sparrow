"""The scout: the mechanical part of finding a release, done by code.

It runs the obvious searches for the wanted episodes, reads every result
name, peeks inside the most promising packs for the actual episode file,
drops what cannot satisfy the request and ranks the rest. A cheap model picks
from the short list it returns, and a stronger model reviews that pick
(node_tools.propose_release). Nothing here is final: the agents decide.
"""

from __future__ import annotations

import math
import re
import urllib.parse

import httpx

from ..services.release_parser import parse_release_name
from ..services.search_engine import _query as apibay_query
from .release_match import episode_in, other_season, sequel_of

QUALITY_RANK = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4}
ROWS = 8
ENOUGH = 4  # healthy candidates that end the search early
PEEKS = 3
DUB_ONLY = re.compile(r"(?i)\b(eng(lish)?[\s._-]?dub(bed)?|dubbed|dub[\s._-]?only)\b")
DUAL = re.compile(r"(?i)\b(dual[\s._-]?audio|multi[\s._-]?audio|jap(anese)?|jpn|original[\s._-]?audio)\b")
SUBS = re.compile(r"(?i)\b(multi[\s._-]?subs?|e?subs?|eng[\s._-]?subs?|subbed|softsubs?|cr|nf|amzn|dsnp|hidive|web[\s._-]?dl)\b")
ALTERNATE = re.compile(r"(?i)\b(dc|director'?s[\s._-]?cut|extended|uncut|alt(ernate)?)\b")
VIDEO = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm")


def _plain(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _rank(quality):
    return QUALITY_RANK.get(quality, 0)


def queries(titles, targets, media_type, year=None):
    """The searches a careful person would try first."""
    out = []
    season, episode = targets[0] if targets else (1, 1)
    for title in titles[:2]:
        if media_type == "movie":
            out += [f"{title} {year}" if year else title]
            continue
        if len(targets) <= 2:
            for s, e in targets:
                out += [f"{title} S{s:02d}E{e:02d}"]
                if s == 1:
                    out += [f"{title} {e:02d}"]  # anime-style absolute numbering
        out += [f"{title} S{season:02d}"]
    out += [titles[0]] if titles else []  # packs and complete series often carry only the title
    return list(dict.fromkeys(q.strip() for q in out if q.strip()))


def judge(row, job, targets, titles=(), exclude=()):
    """A parsed result with its coverage, or None when it cannot serve.

    coverage: "single" (the release is one wanted episode), "pack" (needs a peek)."""
    season = targets[0][0] if targets else 1
    name = row.get("name") or ""
    try:
        seeders, size = int(row.get("seeders") or 0), int(row.get("size") or 0)
    except (TypeError, ValueError):
        return None
    if seeders < 1:
        return None
    parsed = parse_release_name(name, size)
    if parsed.risk_flags:
        return None
    if _rank(parsed.quality) and _rank(parsed.quality) < _rank(job.min_quality):
        return None
    if job.audio_pref == "original" and DUB_ONLY.search(name) and not DUAL.search(name):
        return None
    if job.year and parsed.year and abs(parsed.year - job.year) > 1 and (job.media_type == "movie" or season == 1):
        return None  # another film or a remake of the series
    if job.media_type == "movie":
        coverage = "single"
    elif (other_season(name, season) or sequel_of(name, titles, season)) and not parsed.is_complete_series:
        return None
    elif any(_plain(other) and _plain(other) in _plain(name) for other in exclude):
        return None  # another season's own title
    elif any(episode_in(name, s, e) for s, e in targets) and not parsed.is_season_pack:
        coverage = "single"
    elif parsed.is_complete_series or parsed.is_season_pack or int(row.get("num_files") or 0) > 3:
        coverage = "pack"
    else:
        return None
    return {
        "apibay_id": str(row.get("id")),
        "info_hash": (row.get("info_hash") or "").lower(),
        "name": name,
        "seeders": seeders,
        "size": size,
        "files": int(row.get("num_files") or 0),
        "quality": parsed.quality,
        "source": parsed.source,
        "coverage": coverage,
        "dual": bool(DUAL.search(name)),
        "subs": bool(SUBS.search(name)),
    }


def score(candidate, job):
    """Higher is better: fit to the quality window, swarm health, likely
    original audio and English subtitles, and simplicity."""
    rank, preferred = _rank(candidate["quality"]), _rank(job.preferred_quality)
    fit = 1.5 if not rank else 3 if rank == preferred else 2.5 if rank > preferred else 2
    return (
        2 * fit
        + math.log2(candidate["seeders"] + 1)
        + (1 if candidate["subs"] else 0)
        + (0.5 if candidate["dual"] else 0)
        + (0.5 if candidate["coverage"] == "single" else 0)
        + 2 * (len(candidate.get("covers") or [1]) - 1)  # more wanted episodes in one transfer
    )


async def peek(apibay_id, targets, media_type):
    """The wanted episodes' files inside a pack ({(season, episode): file});
    empty when none are there or the listing is unknown."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"https://apibay.org/f.php?id={urllib.parse.quote(apibay_id)}")
        listing = response.json()
    from .tools import _apibay_indexed_value as value

    files = []
    for entry in listing if isinstance(listing, list) else []:
        if not isinstance(entry, dict):
            continue
        name, size = value(entry.get("name"), ""), value(entry.get("size"), 0)
        if not name or name == "Filelist not found" or not str(name).lower().endswith(VIDEO):
            continue
        if "sample" in str(name).lower():
            continue
        for target in ([None] if media_type == "movie" else targets):
            if target is None or episode_in(str(name), *target):
                files.append((target, {"name": str(name), "size": int(size or 0)}))
    chosen = {}
    for target, file in files:
        current = chosen.get(target)
        # The standard cut over alternates, then the largest: not a sample.
        key = (not ALTERNATE.search(file["name"]), file["size"])
        if current is None or key > (not ALTERNATE.search(current["name"]), current["size"]):
            chosen[target] = file
    return chosen


async def scout(tb, job, targets, titles, extra_queries=(), searches=6, exclude=()):
    """Search, judge, peek and rank; returns (rows, searched, dropped)."""
    plan = list(dict.fromkeys([*extra_queries, *queries(titles, targets, job.media_type, job.year)]))[:searches]
    found, searched = {}, []
    for query in plan:
        try:
            await tb.rate_limit_search()
        except Exception:
            if searched:
                break  # searches are rationed: rank what was found
            raise
        try:
            raw = await apibay_query(query, strict=True)
        except Exception:
            continue
        searched.append(query)
        for row in raw or []:
            candidate = judge(row, job, targets, titles, exclude)
            if candidate and candidate["info_hash"] and candidate["info_hash"] not in found:
                found[candidate["info_hash"]] = candidate
        # Searches are rationed across every request: stop once there is a
        # real choice of healthy copies.
        if len([c for c in found.values() if c["seeders"] >= 5]) >= ENOUGH:
            break
    ranked = sorted(found.values(), key=lambda c: -score(c, job))
    # The person's size cap applies to the file that will be kept.
    cap = float(((job.preferences or {}).get("values") or {}).get("max_file_size_gb") or 0)
    rows, peeks = [], 0
    for candidate in ranked:
        if len(rows) >= ROWS:
            break
        if candidate["coverage"] == "pack":
            if peeks >= PEEKS:
                continue
            peeks += 1
            try:
                chosen = await peek(candidate["apibay_id"], targets, job.media_type)
            except (httpx.HTTPError, ValueError):
                chosen = {}
            if not chosen:
                continue  # no wanted episode in it, or its listing is unknown
            files = list(chosen.values())
            candidate = {
                **candidate,
                "chosen": [f["name"] for f in files],
                "covers": [list(t) for t in chosen if t],
                "episode_size": max(f["size"] for f in files),
            }
        else:
            candidate = {**candidate, "episode_size": candidate["size"]}
        if cap and candidate["episode_size"] > cap * 1e9:
            continue
        rows.append(candidate)
    rows.sort(key=lambda c: -score(c, job))  # packs now know what they cover
    for number, row in enumerate(rows, 1):
        row["rid"] = f"r{number}"
    return rows, searched, len(found) - len(rows)


async def titles_for(tb, job, season):
    """Names to search by (the title, then romanised and English
    alternatives) and other seasons' names to exclude."""
    titles, exclude = [job.title], []
    kind = "tv" if job.media_type == "tv" else "movie"
    try:
        data = await tb.tmdb_get(f"/{kind}/{job.tmdb_id}/alternative_titles")
    except Exception:
        data = {}
    for alternative in data.get("results") or data.get("titles") or []:
        label, name = str(alternative.get("type") or "").lower(), alternative.get("title") or ""
        other = re.search(r"season\s*(\d+)", label)
        if other and int(other.group(1)) != season:
            exclude.append(name)
        elif alternative.get("iso_3166_1") in ("JP", "US", "GB") and "abbreviation" not in label and name:
            titles.append(name)
    return list(dict.fromkeys(titles))[:3], exclude


def table(rows):
    """The short list in a few tokens a row."""
    lines = []
    for row in rows:
        covered = len(row.get("chosen") or [])
        what = "single release" if row["coverage"] == "single" else f"pack of {row['files']} files, {covered} wanted file{'s' if covered != 1 else ''} chosen"
        hints = ", ".join(h for h, on in (("original audio named", row["dual"]), ("subtitles likely", row["subs"])) if on)
        lines.append(
            f"{row['rid']} · {row['episode_size'] / 1e9:.2f} GB · {row['seeders']} seeds · {row['quality']} {row['source']} · "
            f"{what}{' · ' + hints if hints else ''} · {row['name'][:90]}"
        )
    return "\n".join(lines)
