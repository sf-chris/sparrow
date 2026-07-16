"""
Release name parser — turns messy torrent release names into structured facts.

This replaces the CWM's self-evolving regex strategies. The design is tiered:

  Tier 0 (free):   deterministic regex parsing handles conventional names
                   (SxxExx, season packs, complete series, quality/codec tags).
  Tier 1 (Haiku):  names the regex tier can't parse confidently are batch-sent
                   to claude-haiku for structured extraction (~$0.0001/name).
  Cache:           every parse (regex or AI) is cached in sqlite by name hash,
                   so a given release name is never paid for twice.

The output answers the one question the pipeline actually needs:
"exactly which episodes does this torrent cover, at what quality?"
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

HAIKU_MODEL = "claude-haiku-4-5-20251001"

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".webm"}

RISK_TERMS = ("cam", "hdcam", "camrip", "telesync", "hdts", "sample", "trailer", "screener")


@dataclass
class ParsedRelease:
    raw_name: str
    media_type: str = "unknown"          # movie | tv | unknown
    title: str = ""                      # cleaned title guess
    year: Optional[int] = None
    seasons: list[int] = field(default_factory=list)      # seasons this release covers
    episodes: list[list[int]] = field(default_factory=list)  # explicit [season, episode] pairs
    is_season_pack: bool = False         # covers full season(s), no explicit episode list
    is_complete_series: bool = False
    is_absolute_numbering: bool = False  # anime-style "Title - 105" numbering
    quality: str = "unknown"             # 2160p | 1080p | 720p | 480p | unknown
    source: str = "unknown"              # REMUX | BLURAY | WEB-DL | WEBRIP | HDTV | ...
    codec: str = "unknown"               # x265 | x264 | av1 | unknown
    group: str = ""                      # release group
    risk_flags: list[str] = field(default_factory=list)
    confidence: float = 0.0              # 0-1: how sure we are about coverage
    parsed_by: str = "regex"             # regex | ai

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ParsedRelease":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def covers(self, season: int, episode: int) -> bool:
        """Does this release contain the given episode?"""
        if self.is_complete_series:
            return True
        if [season, episode] in self.episodes:
            return True
        if self.is_season_pack and season in self.seasons and not self.episodes:
            return True
        return False

    def covered_episodes(self, season: int, episode_count: int) -> set[int]:
        """Episodes of `season` this release covers, given the season's size."""
        if self.is_complete_series:
            return set(range(1, episode_count + 1))
        if self.is_season_pack and season in self.seasons and not self.episodes:
            return set(range(1, episode_count + 1))
        return {e for s, e in self.episodes if s == season}


def name_hash(name: str) -> str:
    return hashlib.sha256(name.strip().lower().encode()).hexdigest()[:32]


# ─── Tier 0: deterministic parsing ───────────────────────────────────────────

_QUALITY_PATTERNS = [
    (re.compile(r"\b(2160p|4k|uhd)\b", re.I), "2160p"),
    (re.compile(r"\b1080p?\b", re.I), "1080p"),
    (re.compile(r"\b720p?\b", re.I), "720p"),
    (re.compile(r"\b(480p|576p|dvdrip)\b", re.I), "480p"),
]

_SOURCE_PATTERNS = [
    (re.compile(r"\bremux\b", re.I), "REMUX"),
    (re.compile(r"\b(blu-?ray|bdrip|brrip|bd)\b", re.I), "BLURAY"),
    (re.compile(r"\bweb-?dl\b", re.I), "WEB-DL"),
    (re.compile(r"\bweb-?rip\b", re.I), "WEBRIP"),
    (re.compile(r"\bweb\b", re.I), "WEB-DL"),
    (re.compile(r"\bhdtv\b", re.I), "HDTV"),
    (re.compile(r"\bdvd(rip)?\b", re.I), "DVD"),
]

_CODEC_PATTERNS = [
    (re.compile(r"\b(x265|h\.?265|hevc)\b", re.I), "x265"),
    (re.compile(r"\b(x264|h\.?264|avc)\b", re.I), "x264"),
    (re.compile(r"\bav1\b", re.I), "av1"),
]

# S01E05, S01E05E06, S01E05-E07, 1x05
_RE_SXXEXX = re.compile(r"\bS(\d{1,2})[\s._-]*E(\d{1,3})(?:[-_.\s]*E?(\d{1,3}))?\b", re.I)
_RE_NXNN = re.compile(r"\b(\d{1,2})x(\d{2,3})\b", re.I)
# S01 (pack), S01-S03, S01.S02.S03
_RE_SEASON_ONLY = re.compile(r"\bS(\d{1,2})(?:\s*[-._]\s*S?(\d{1,2}))?\b", re.I)
_RE_SEASON_WORD = re.compile(r"\bseason[\s._]*(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\b", re.I)
_RE_EPISODE_WORD = re.compile(r"\bepisode[\s._]*(\d{1,3})\b", re.I)
_RE_COMPLETE = re.compile(r"\b(complete\s*(series|collection|show)?|all\s*seasons|full\s*series|batch)\b", re.I)
_RE_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_RE_GROUP_SUFFIX = re.compile(r"-([A-Za-z0-9]+)\s*(?:\[[^\]]*\])?$")
# Tokens that look like a "-GROUP" suffix but are actually format tags
_NOT_GROUPS = {
    "dl", "rip", "ray", "bluray", "webdl", "webrip", "hdtv", "remux",
    "x264", "x265", "h264", "h265", "hevc", "av1", "aac", "ddp", "dts",
    "atmos", "hdr", "sdr", "10bit", "8bit",
}
_RE_GROUP_PREFIX = re.compile(r"^\[([^\]]+)\]")
# anime: "Title - 05" / "Title - 105 [1080p]" (episode number without S marker)
_RE_ANIME_EP = re.compile(r"[-\s]\s*(\d{1,4})\s*(?:\(|\[|v\d|$)", re.I)

_TITLE_BREAKERS = re.compile(
    r"\b(S\d{1,2}([\s._-]*E\d{1,3})?|season[\s._]*\d{1,2}|episode[\s._]*\d{1,3}|"
    r"\d{1,2}x\d{2,3}|(19|20)\d{2}|2160p|4k|1080p|720p|480p|576p|complete|"
    r"web-?dl|web-?rip|blu-?ray|bdrip|brrip|hdtv|remux|dvdrip|x26[45]|h\.?26[45]|hevc|av1)\b",
    re.I,
)


def _clean_title(fragment: str) -> str:
    t = re.sub(r"^\[[^\]]*\]\s*", "", fragment)      # strip fansub group prefix
    t = re.sub(r"[._]+", " ", t)
    t = re.sub(r"[\[\(].*?[\]\)]", " ", t)
    t = re.sub(r"[-–]\s*$", "", t.strip())
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" -_.").strip()


def parse_release_name(name: str, size_bytes: int = 0) -> ParsedRelease:
    """Deterministic tier-0 parse. Sets confidence low when coverage is unclear."""
    p = ParsedRelease(raw_name=name)
    work = name.strip()

    for rx, val in _QUALITY_PATTERNS:
        if rx.search(work):
            p.quality = val
            break
    for rx, val in _SOURCE_PATTERNS:
        if rx.search(work):
            p.source = val
            break
    for rx, val in _CODEC_PATTERNS:
        if rx.search(work):
            p.codec = val
            break

    low = work.lower()
    p.risk_flags = [t for t in RISK_TERMS if re.search(rf"\b{t}\b", low)]

    m = _RE_GROUP_PREFIX.search(work)
    if m:
        p.group = m.group(1)
    else:
        m = _RE_GROUP_SUFFIX.search(work)
        if m and not m.group(1).isdigit() and m.group(1).lower() not in _NOT_GROUPS:
            p.group = m.group(1)

    ym = _RE_YEAR.search(work)
    if ym:
        p.year = int(ym.group(1))

    # Explicit episodes: SxxExx (possibly a range) — may occur multiple times
    ep_matches = list(_RE_SXXEXX.finditer(work))
    if ep_matches:
        for m in ep_matches:
            season = int(m.group(1))
            e1 = int(m.group(2))
            e2 = int(m.group(3)) if m.group(3) else e1
            if e2 < e1:
                e1, e2 = e2, e1
            if e2 - e1 > 60:   # implausible range; treat as single
                e2 = e1
            for e in range(e1, e2 + 1):
                if [season, e] not in p.episodes:
                    p.episodes.append([season, e])
            if season not in p.seasons:
                p.seasons.append(season)
        p.media_type = "tv"
        p.confidence = 0.95
    else:
        nm = _RE_NXNN.search(work)
        if nm:
            season, ep = int(nm.group(1)), int(nm.group(2))
            p.episodes.append([season, ep])
            p.seasons.append(season)
            p.media_type = "tv"
            p.confidence = 0.9

    # Season pack / complete series detection (no explicit episode markers)
    if not p.episodes:
        if _RE_COMPLETE.search(work):
            p.is_complete_series = True
            p.media_type = "tv"
            p.confidence = 0.8
        sm = _RE_SEASON_ONLY.search(work) or _RE_SEASON_WORD.search(work)
        if sm:
            s1 = int(sm.group(1))
            s2 = int(sm.group(2)) if sm.group(2) else s1
            if s2 < s1:
                s1, s2 = s2, s1
            if s2 - s1 > 40:
                s2 = s1
            p.seasons = list(range(s1, s2 + 1))
            p.is_season_pack = True
            p.media_type = "tv"
            if len(p.seasons) > 1:
                p.is_complete_series = p.is_complete_series or False
            p.confidence = max(p.confidence, 0.85)
        em = _RE_EPISODE_WORD.search(work)
        if em and p.seasons and len(p.seasons) == 1:
            p.episodes = [[p.seasons[0], int(em.group(1))]]
            p.is_season_pack = False
            p.confidence = 0.9

    # Title extraction: everything before the first structural marker
    bm = _TITLE_BREAKERS.search(work)
    fragment = work[: bm.start()] if bm else work
    p.title = _clean_title(fragment)

    if p.media_type == "unknown":
        if p.year and not p.seasons and not p.episodes:
            # Year + quality tags and no TV markers → very likely a movie
            p.media_type = "movie"
            p.confidence = 0.8 if p.quality != "unknown" else 0.6
        else:
            # Possibly anime absolute numbering: "Title - 105"
            am = _RE_ANIME_EP.search(work)
            if am and p.group and not p.year:
                p.media_type = "tv"
                p.is_absolute_numbering = True
                p.episodes = [[0, int(am.group(1))]]  # season unknown
                p.confidence = 0.45
            else:
                p.confidence = 0.2

    if not p.title:
        p.confidence = min(p.confidence, 0.3)
    return p


# ─── Tier 1: Haiku batch parsing for the hard cases ──────────────────────────

_AI_TOOL = {
    "name": "report_parsed_releases",
    "description": "Report structured parsing results for torrent release names.",
    "input_schema": {
        "type": "object",
        "properties": {
            "releases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "Index of the name in the input list"},
                        "media_type": {"type": "string", "enum": ["movie", "tv", "unknown"]},
                        "title": {"type": "string"},
                        "year": {"type": ["integer", "null"]},
                        "seasons": {"type": "array", "items": {"type": "integer"}},
                        "episodes": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "integer"}},
                            "description": "[season, episode] pairs explicitly covered. Empty for full packs.",
                        },
                        "is_season_pack": {"type": "boolean"},
                        "is_complete_series": {"type": "boolean"},
                        "is_absolute_numbering": {"type": "boolean"},
                        "quality": {"type": "string", "enum": ["2160p", "1080p", "720p", "480p", "unknown"]},
                        "group": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["index", "media_type", "title", "confidence"],
                },
            }
        },
        "required": ["releases"],
    },
}

_AI_SYSTEM = (
    "You parse torrent release names into structured facts. You are expert in "
    "every naming convention: scene releases, fansub anime (absolute episode "
    "numbering, [Group] prefixes, batch markers), season packs (S01, Stagione 1, "
    "Saison 1), multi-season ranges (S01-S05), complete-series bundles, and "
    "multi-episode files (S01E01E02, E01-E03). For anime absolute numbering, "
    "report episodes as [0, N] pairs (season 0 = unknown/absolute) and set "
    "is_absolute_numbering. Be precise about exactly which episodes a name "
    "covers — that is the entire point. If a name gives no episode information "
    "but is clearly a full season, set is_season_pack with the season numbers."
)


async def _ai_parse_batch(names: list[str], api_key: str) -> dict[int, dict]:
    """One Haiku call parsing up to ~40 names. Returns index -> parsed dict."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    numbered = "\n".join(f"{i}: {n}" for i, n in enumerate(names))
    resp = await client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=4000,
        system=_AI_SYSTEM,
        tools=[_AI_TOOL],
        tool_choice={"type": "tool", "name": "report_parsed_releases"},
        messages=[{
            "role": "user",
            "content": f"Parse these torrent release names:\n{numbered}",
        }],
    )
    out: dict[int, dict] = {}
    for block in resp.content:
        if block.type == "tool_use":
            for rel in block.input.get("releases", []):
                idx = rel.get("index")
                if isinstance(idx, int) and 0 <= idx < len(names):
                    out[idx] = rel
    return out


AI_CONFIDENCE_THRESHOLD = 0.7


async def parse_releases(
    names: list[str],
    storage=None,
    anthropic_api_key: str = "",
    ai_batch_limit: int = 40,
) -> list[ParsedRelease]:
    """Parse a batch of release names: cache → regex → Haiku for the rest.

    Order of results matches the input. Never raises on AI failure — falls
    back to the regex parse.
    """
    results: list[Optional[ParsedRelease]] = [None] * len(names)
    needs_ai: list[int] = []

    for i, name in enumerate(names):
        h = name_hash(name)
        cached = storage.get_parsed_release(h) if storage else None
        if cached:
            results[i] = ParsedRelease.from_dict(cached)
            continue
        parsed = parse_release_name(name)
        results[i] = parsed
        if parsed.confidence < AI_CONFIDENCE_THRESHOLD and anthropic_api_key:
            needs_ai.append(i)
        elif storage:
            storage.put_parsed_release(h, name, parsed.to_dict(), "regex")

    if needs_ai and anthropic_api_key:
        for start in range(0, len(needs_ai), ai_batch_limit):
            chunk = needs_ai[start:start + ai_batch_limit]
            chunk_names = [names[i] for i in chunk]
            try:
                ai_out = await _ai_parse_batch(chunk_names, anthropic_api_key)
            except Exception:
                ai_out = {}
            for local_idx, orig_idx in enumerate(chunk):
                base = results[orig_idx]
                rel = ai_out.get(local_idx)
                if rel:
                    merged = ParsedRelease(
                        raw_name=names[orig_idx],
                        media_type=rel.get("media_type", base.media_type),
                        title=rel.get("title") or base.title,
                        year=rel.get("year", base.year),
                        seasons=rel.get("seasons") or base.seasons,
                        episodes=[list(e) for e in (rel.get("episodes") or base.episodes)],
                        is_season_pack=rel.get("is_season_pack", base.is_season_pack),
                        is_complete_series=rel.get("is_complete_series", base.is_complete_series),
                        is_absolute_numbering=rel.get("is_absolute_numbering", base.is_absolute_numbering),
                        quality=rel.get("quality", base.quality) if rel.get("quality", "unknown") != "unknown" else base.quality,
                        source=base.source,
                        codec=base.codec,
                        group=rel.get("group") or base.group,
                        risk_flags=base.risk_flags,
                        confidence=float(rel.get("confidence", 0.75)),
                        parsed_by="ai",
                    )
                    results[orig_idx] = merged
                if storage:
                    final = results[orig_idx]
                    storage.put_parsed_release(
                        name_hash(names[orig_idx]), names[orig_idx],
                        final.to_dict(), final.parsed_by,
                    )
    elif needs_ai and storage:
        # No API key: cache the low-confidence regex parses anyway so we don't
        # re-parse, but mark them as regex so a future AI pass can improve them.
        for i in needs_ai:
            storage.put_parsed_release(name_hash(names[i]), names[i], results[i].to_dict(), "regex")

    return results  # type: ignore[return-value]


async def parse_one(name: str, storage=None, anthropic_api_key: str = "") -> ParsedRelease:
    return (await parse_releases([name], storage, anthropic_api_key))[0]
