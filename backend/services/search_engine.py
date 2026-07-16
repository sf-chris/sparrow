"""
Smart search engine — deterministic query plans with live event streaming.

Flow:
  1. Check library + active downloads (pure Python, free)
  2. Run built-in query variants (pure Python, free)
  3. TMDB title correction if everything missed (free API)
  4. Last resort: one cheap Haiku call for alternate query phrasings

Events: library_hit | downloading | trying | hit | miss | ping |
        evolving | thinking | evolved | evolving_failed | no_results | done | error
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import AsyncIterator, Optional
import httpx

from ..models import SearchResult, Quality, MediaType
from .torrent_client import build_magnet

log = logging.getLogger("sparrow.search")

APIBAY    = "https://apibay.org"
SEARCH_LOG = "search_log.json"


# ─── apibay ──────────────────────────────────────────────────────────────────

async def _query(q: str, cat: str = "0") -> list[dict]:
    url = f"{APIBAY}/q.php?q={urllib.parse.quote(q)}&cat={cat}"
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(url)
            data = r.json()
            if isinstance(data, list):
                return [
                    x for x in data
                    if x.get("info_hash") and x["info_hash"] != "0" * 40
                    and int(x.get("seeders", 0)) > 0
                ]
    except Exception:
        pass
    return []


async def _query_with_pings(q: str, cat: str) -> AsyncIterator[dict]:
    """Run apibay query with 2s keepalive pings. Yields pings then {"type":"_result","raw":[...]}."""
    query_task = asyncio.create_task(_query(q, cat))
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 6.5
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            query_task.cancel()
            try:
                await query_task
            except Exception:
                pass
            yield {"type": "_result", "raw": []}
            return
        try:
            raw = await asyncio.wait_for(asyncio.shield(query_task), timeout=min(2.0, remaining))
            yield {"type": "_result", "raw": raw}
            return
        except asyncio.TimeoutError:
            yield {"type": "ping"}
        except Exception:
            query_task.cancel()
            yield {"type": "_result", "raw": []}
            return


def _to_result(r: dict) -> SearchResult:
    name = r.get("name", "")
    h    = r.get("info_hash", "")
    try:
        seeders  = int(r.get("seeders",  0))
        leechers = int(r.get("leechers", 0))
        size     = int(r.get("size",     0))
        added    = int(r.get("added",    0))
    except (ValueError, TypeError):
        seeders = leechers = size = added = 0
    return SearchResult(
        id=r.get("id", h), name=name, info_hash=h,
        seeders=seeders, leechers=leechers, size_bytes=size, added_ts=added,
        category=r.get("category", ""), source="apibay",
        imdb_id=r.get("imdb") or None, uploader=r.get("username", ""),
        magnet_url=build_magnet(h, name),
    )

def _detect_quality(name: str) -> str:
    n = name.lower()
    for q in ("2160p", "1080p", "720p", "480p"):
        if q in n:
            return q
    return "unknown"


def _detect_source(name: str) -> str:
    n = name.lower()
    for source in ("remux", "bluray", "web-dl", "webrip", "bdrip", "hdtv", "hdrip", "dvdrip"):
        if source in n:
            return source.upper()
    return "unknown"


def _detect_codec(name: str) -> str:
    n = name.lower()
    if "x265" in n or "h265" in n or "hevc" in n:
        return "x265"
    if "x264" in n or "h264" in n or "avc" in n:
        return "x264"
    return "unknown"


def _looks_like_season_pack(name: str, season: int | None = None) -> bool:
    n = name.lower()
    if season is not None:
        s_tag = f"s{season:02d}"
        if s_tag not in n and f"season {season}" not in n:
            return False
    has_season = bool(re.search(r'\bs\d{1,2}\b|\bseason\s+\d+\b', n))
    has_episode = bool(re.search(r'\bs\d{1,2}e\d{1,3}\b', n))
    pack_words = ("complete", "season", "pack", "batch")
    return has_season and (not has_episode or any(w in n for w in pack_words))


def _expected_season_size_gb(episode_count: int, quality: Quality | str, prefer_smaller: bool) -> tuple[float, float]:
    q = quality.value if isinstance(quality, Quality) else quality
    per_episode = {
        "2160p": (2.0, 9.0),
        "1080p": (0.45, 3.0) if prefer_smaller else (0.8, 4.5),
        "720p": (0.25, 1.8),
        "480p": (0.15, 1.0),
        "any": (0.15, 5.0),
    }.get(q, (0.25, 4.0))
    return per_episode[0] * max(1, episode_count), per_episode[1] * max(1, episode_count)


def score_release(
    result: SearchResult,
    quality: Quality = Quality.Q_1080P,
    prefer_smaller: bool = False,
    season: int | None = None,
    episode_count: int | None = None,
) -> dict:
    """Score a torrent candidate for consumer-grade automatic selection."""
    qs = {"2160p": 4, "1080p": 3, "720p": 2, "480p": 1, "unknown": 2, "any": 2}
    wanted = quality.value if isinstance(quality, Quality) else str(quality)
    actual_quality = _detect_quality(result.name)
    quality_score = max(0, 24 - abs(qs.get(actual_quality, 2) - qs.get(wanted, 3)) * 10)
    seeder_score = min(30, result.seeders * 1.5)
    source = _detect_source(result.name)
    source_score = {
        "WEB-DL": 10, "BLURAY": 9, "REMUX": 8, "WEBRIP": 7, "BDRIP": 6,
        "HDTV": 4, "HDRIP": 4, "DVDRIP": 2,
    }.get(source, 3)
    codec = _detect_codec(result.name)
    codec_score = 8 if prefer_smaller and codec == "x265" else 4 if codec in ("x264", "x265") else 1

    size_gb = result.size_bytes / 1024 ** 3 if result.size_bytes else 0
    size_score = 5
    size_reason = "unknown size"
    if episode_count:
        low, high = _expected_season_size_gb(episode_count, quality, prefer_smaller)
        if result.size_bytes <= 0:
            size_score = 2
        elif low <= size_gb <= high:
            size_score = 14
            size_reason = f"within expected {low:.1f}-{high:.1f} GB"
        elif size_gb > high:
            size_score = -18
            size_reason = f"too large for {episode_count} episodes"
        else:
            size_score = -8
            size_reason = f"too small for {episode_count} episodes"
    elif result.size_bytes > 0:
        size_score = 10 if prefer_smaller and size_gb <= 8 else 8
        size_reason = f"{size_gb:.1f} GB"

    pack_score = 10 if _looks_like_season_pack(result.name, season) else 0
    score = quality_score + seeder_score + source_score + codec_score + size_score + pack_score
    return {
        "score": round(score, 2),
        "quality": actual_quality,
        "source": source,
        "codec": codec,
        "size_gb": round(size_gb, 2),
        "season_pack": _looks_like_season_pack(result.name, season),
        "reason": f"{actual_quality}, {result.seeders} seeders, {source}, {codec}, {size_reason}",
    }


def rank_results(
    results: list[SearchResult],
    quality: Quality = Quality.Q_1080P,
    prefer_smaller: bool = False,
    season: int | None = None,
    episode_count: int | None = None,
) -> list[tuple[SearchResult, dict]]:
    scored = [(r, score_release(r, quality, prefer_smaller, season, episode_count)) for r in results]
    return sorted(scored, key=lambda pair: pair[1]["score"], reverse=True)


def choose_season_strategy(
    pack_results: list[SearchResult],
    episode_results: list[SearchResult],
    quality: Quality = Quality.Q_1080P,
    query: str = "",
    title: str = "",
    media_type: MediaType = MediaType.TV,
    season: int | None = None,
    episode_count: int = 0,
    prefer_smaller: bool = False,
    prefer_season_packs: bool = True,
    season_pack_size_limit_gb: float = 0.0,
    year: int | None = None,
    tmdb_id: int | None = None,
    imdb_id: str | None = None,
    aliases: list[str] | None = None,
) -> dict:
    """Choose a season pack when it is good enough; otherwise fall back to episodes."""
    from .release_decision_engine import ReleaseContext, choose_season_release

    context = ReleaseContext(
        query=query or title,
        title=title or query,
        media_type=media_type,
        quality=quality,
        season=season,
        episode_count=episode_count,
        year=year,
        tmdb_id=tmdb_id,
        imdb_id=imdb_id,
        aliases=aliases or [],
        prefer_smaller=prefer_smaller,
        prefer_season_packs=prefer_season_packs,
        season_pack_size_limit_gb=season_pack_size_limit_gb,
    )
    return choose_season_release(pack_results, episode_results, context)


# ─── Library / download pre-check ────────────────────────────────────────────

def _fuzzy_match(a: str, b: str) -> bool:
    stop = {"the", "a", "an", "of", "and", "or", "in", "on", "at", "to"}
    wa = set(re.findall(r'\w+', a.lower())) - stop
    wb = set(re.findall(r'\w+', b.lower())) - stop
    if not wa or not wb:
        return False
    return len(wa & wb) >= min(2, len(wa), len(wb))


def _check_library(query: str, data_dir: str) -> list[dict]:
    p = Path(data_dir) / "library.json"
    if not p.exists():
        return []
    try:
        library = json.loads(p.read_text())
    except Exception:
        return []
    q = query.lower()
    return [
        {"id": item.get("id", ""), "title": item.get("title", ""), "media_type": item.get("media_type", "")}
        for item in library
        if (title := item.get("title", "").lower()) and (q in title or title in q or _fuzzy_match(q, title))
    ]


def _check_downloads(query: str, data_dir: str) -> list[dict]:
    p = Path(data_dir) / "downloads.json"
    if not p.exists():
        return []
    try:
        downloads = json.loads(p.read_text())
    except Exception:
        return []
    q = query.lower()
    active = {"queued", "downloading", "seeding", "organizing"}
    return [
        {"id": dl.get("id", ""), "name": dl.get("name", ""), "status": dl.get("status", "")}
        for dl in downloads
        if dl.get("status") in active
        and (name := dl.get("name", "").lower())
        and (q in name or name in q or _fuzzy_match(q, name))
    ]


# ─── Built-in query plan ─────────────────────────────────────────────────────
# These variants cover everything the old self-evolving CWM strategies learned:
# natural-language season/episode → scene format, compact tags, season packs,
# article trimming, and quality suffixes. Deterministic and free.

def _builtin_query_plan(query: str, media_type: str, quality: str) -> list[tuple[str, list[str]]]:
    q = query.strip()
    plans: list[tuple[str, list[str]]] = [("bare", [q])]

    m = re.search(r"(.*?)\s+season\s*(\d{1,2})\s*(?:episode|ep\.?)\s*(\d{1,3})\s*$", q, re.I)
    if m:
        t, s, e = m.group(1).strip(), int(m.group(2)), int(m.group(3))
        plans.append(("episode_reformat", [f"{t} S{s:02d}E{e:02d}", f"{t} {s}x{e:02d}"]))

    m = re.search(r"(.*?)\s+season\s*(\d{1,2})\s*$", q, re.I)
    if m:
        t, s = m.group(1).strip(), int(m.group(2))
        plans.append(("season_pack", [f"{t} S{s:02d}", f"{t} S{s:02d} complete", f"{t} season {s}"]))

    m = re.search(r"(.*?)\s+s(\d{1,2})\s*[ex]\s*(\d{1,3})\s*$", q, re.I)
    if m:
        t, s, e = m.group(1).strip(), int(m.group(2)), int(m.group(3))
        plans.append(("sxxexx_normalise", [f"{t} S{s:02d}E{e:02d}"]))

    if q.lower().startswith("the "):
        plans.append(("article_trim", [q[4:]]))

    if quality and quality not in ("any", "unknown"):
        plans.append(("quality_tag", [f"{q} {quality}"]))

    return plans


# ─── Search log ──────────────────────────────────────────────────────────────

def _log(data_dir: str, entry: dict) -> None:
    p = Path(data_dir) / SEARCH_LOG
    existing: list = []
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except Exception:
            pass
    existing.append(entry)
    p.write_text(json.dumps(existing[-500:], indent=2))


# ─── AI alternate queries (one cheap Haiku call) ─────────────────────────────

async def _ai_alt_queries_stream(
    original_query: str,
    context: dict,
    tried: list[str],
    api_key: str,
) -> AsyncIterator[dict]:
    """
    Ask Haiku for alternate query phrasings (alt titles, romanizations,
    abbreviations). Yields {"type":"_result","queries":[...]} at the end.
    Never raises.
    """
    if not api_key:
        yield {"type": "_result", "queries": []}
        return
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
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
            messages=[{"role": "user", "content": (
                f"Torrent search for \"{original_query}\" (context: {context}) found nothing.\n"
                f"Already tried:\n{chr(10).join(f'  {q}' for q in tried)}\n\n"
                "Suggest up to 5 alternate search queries that could work: "
                "alternative or original-language titles, romanized titles for "
                "anime, common abbreviations, franchise names, or looser phrasings. "
                "Queries only."
            )}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                queries = [q for q in block.input.get("queries", []) if isinstance(q, str)][:5]
                yield {"type": "_result", "queries": queries}
                return
    except Exception as exc:
        log.warning("AI alternate queries failed", extra={"error": str(exc), "query": original_query})

    yield {"type": "_result", "queries": []}


# ─── Alternatives stream (deeper search) ─────────────────────────────────────

async def _suggest_alternatives_stream(
    query: str,
    quality: str,
    api_key: str,
) -> AsyncIterator[dict]:
    """Ask Claude for alternative queries. Yields thinking events then _result."""
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)

        async with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            tools=[{
                "name": "suggest_queries",
                "description": "Suggest alternative search queries to find different versions or releases",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Alternative queries to try on The Pirate Bay"
                        }
                    },
                    "required": ["queries"]
                }
            }],
            messages=[{"role": "user", "content": f"""User searched for "{query}" (quality: {quality}) and wants to find alternatives.

Generate 6-8 alternative search queries that might surface:
- Different quality tiers (2160p remux, 1080p bluray, HEVC/x265 encodes)
- Known release groups (YTS, YIFY, FGT, SPARKS, etc.)
- Extended/director's cut/unrated editions
- Alternative title formats (original language, shortened, romanized)
- Different encodings or sources (WEB-DL, BluRay, HDRip)

Return concise query strings only — no explanations."""}],
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield {"type": "thinking", "text": text}
            final = await stream.get_final_message()

        for block in final.content:
            if block.type == "tool_use" and block.name == "suggest_queries":
                yield {"type": "_result", "queries": block.input.get("queries", [])}
                return

    except Exception as exc:
        log.warning("Alternative suggestion failed", extra={"error": str(exc)})

    yield {"type": "_result", "queries": []}


# ─── Core streaming search ────────────────────────────────────────────────────

async def search_stream(
    query: str,
    media_type: Optional[MediaType] = None,
    quality: Quality = Quality.Q_1080P,
    limit: int = 30,
    data_dir: str = "./data",
    tmdb_api_key: str = "",
    anthropic_api_key: str = "",
    prefer_smaller: bool = False,
) -> AsyncIterator[dict]:
    log.info("Search started", extra={"query": query, "quality": quality.value, "media_type": media_type.value if media_type else None, "prefer_smaller": prefer_smaller})

    mt  = media_type.value if media_type else "unknown"
    ctx = {"media_type": mt, "quality": quality.value, "prefer_smaller": prefer_smaller}
    cat = "0"
    if media_type == MediaType.TV:
        cat = "208" if quality in (Quality.Q_1080P, Quality.Q_2160P) else "500"
    elif media_type == MediaType.MOVIE:
        cat = "207" if quality in (Quality.Q_1080P, Quality.Q_2160P) else "200"

    # ── Step 1: library / download awareness ──────────────────────────────────
    lib_matches = _check_library(query, data_dir)
    if lib_matches:
        log.info("Library match found", extra={"query": query, "matches": [m["title"] for m in lib_matches]})
        yield {"type": "library_hit", "items": lib_matches}
        await asyncio.sleep(0)

    dl_matches = _check_downloads(query, data_dir)
    if dl_matches:
        log.info("Active download match found", extra={"query": query, "matches": [m["name"] for m in dl_matches]})
        yield {"type": "downloading", "items": dl_matches}
        await asyncio.sleep(0)

    # ── Step 2: built-in query variants ───────────────────────────────────────
    plan = _builtin_query_plan(query, mt, quality.value)

    tried: list[str] = []
    results: list[SearchResult] = []
    winner: Optional[str] = None

    for strategy_name, queries in plan:
        for q in queries:
            if q.lower() in [x.lower() for x in tried]:
                continue
            tried.append(q)

            yield {"type": "trying", "strategy": strategy_name, "query": q, "attempt": len(tried)}
            await asyncio.sleep(0)

            raw: list[dict] = []
            async for ev in _query_with_pings(q, cat):
                if ev["type"] == "_result":
                    raw = ev["raw"]
                else:
                    yield ev

            if raw:
                results = list({r["info_hash"]: _to_result(r) for r in raw}.values())
                winner  = strategy_name
                log.info("Search hit", extra={"query": q, "strategy": strategy_name, "count": len(results)})
                yield {"type": "hit", "strategy": strategy_name, "query": q, "count": len(results)}
                break
            else:
                log.debug("Search miss", extra={"query": q, "strategy": strategy_name})
                yield {"type": "miss", "strategy": strategy_name, "query": q}

        if results:
            break

    # ── Step 2.5: TMDB title correction ──────────────────────────────────────
    # If all strategies missed, check whether the query title is actually wrong
    # before spending money on Claude. TMDB is the oracle.
    if not results and tmdb_api_key:
        import difflib
        from .metadata_service import tmdb_quick_suggest, clean_search_query

        clean_title, _, _ = clean_search_query(query)
        suggestions = await tmdb_quick_suggest(clean_title, tmdb_api_key)
        if suggestions:
            canonical = suggestions[0]["title"]
            ratio = difflib.SequenceMatcher(None, clean_title.lower(), canonical.lower()).ratio()
            if canonical.lower().strip() != clean_title.lower().strip() and ratio < 0.85:
                # Preserve season/episode qualifier from original query
                qualifier_m = re.search(
                    r'\b(season\s*\d+|s\d{2}(?:e\d{2})?|episode\s*\d+)\b',
                    query, re.IGNORECASE,
                )
                qualifier = qualifier_m.group(0) if qualifier_m else ""
                corrected_query = f"{canonical} {qualifier}".strip() if qualifier else canonical

                log.info(
                    "Title correction triggered",
                    extra={"original": clean_title, "canonical": canonical, "corrected": corrected_query, "similarity": round(ratio, 2)},
                )
                yield {"type": "title_correction", "original": clean_title, "canonical": corrected_query}
                await asyncio.sleep(0)

                # Re-run the built-in variants with the corrected title
                plan_tc = _builtin_query_plan(corrected_query, mt, quality.value)

                for strategy_name, queries in plan_tc:
                    for q in queries:
                        if q.lower() in [x.lower() for x in tried]:
                            continue
                        tried.append(q)

                        yield {"type": "trying", "strategy": strategy_name, "query": q, "attempt": len(tried)}
                        await asyncio.sleep(0)

                        raw = []
                        async for ev in _query_with_pings(q, cat):
                            if ev["type"] == "_result":
                                raw = ev["raw"]
                            else:
                                yield ev

                        if raw:
                            results = list({r["info_hash"]: _to_result(r) for r in raw}.values())
                            winner = strategy_name
                            log.info("Title-corrected search hit", extra={"query": q, "strategy": strategy_name, "canonical": canonical, "count": len(results)})
                            yield {"type": "hit", "strategy": strategy_name, "query": q, "count": len(results)}
                            break
                        else:
                            yield {"type": "miss", "strategy": strategy_name, "query": q}

                    if results:
                        break

    # ── Step 3: all variants failed → one cheap AI call for alternates ───────
    if not results:
        if anthropic_api_key:
            log.info("All variants failed, asking for alternate queries", extra={"query": query, "attempts": len(tried)})
            yield {"type": "evolving", "message": f"All {len(tried)} queries failed — asking Claude for alternate phrasings..."}
            await asyncio.sleep(0)

            new_queries: list[str] = []
            async for ev in _ai_alt_queries_stream(query, ctx, tried, anthropic_api_key):
                if ev["type"] == "_result":
                    new_queries = ev.get("queries", [])
                else:
                    yield ev

            if new_queries:
                yield {"type": "evolved", "new_queries": new_queries, "strategy_count": 0, "version": 0}

                for q in new_queries:
                    if q.lower() in [x.lower() for x in tried]:
                        continue
                    tried.append(q)
                    yield {"type": "trying", "strategy": "ai_alternate", "query": q, "attempt": len(tried)}
                    await asyncio.sleep(0)

                    raw = []
                    async for ev in _query_with_pings(q, cat):
                        if ev["type"] == "_result":
                            raw = ev["raw"]
                        else:
                            yield ev

                    if raw:
                        results = list({r["info_hash"]: _to_result(r) for r in raw}.values())
                        winner = "ai_alternate"
                        log.info("AI-alternate hit", extra={"query": q, "count": len(results)})
                        yield {"type": "hit", "strategy": "ai_alternate", "query": q, "count": len(results)}
                        break
                    else:
                        yield {"type": "miss", "strategy": "ai_alternate", "query": q}
            else:
                yield {"type": "evolving_failed", "message": "Could not generate alternate queries (check Anthropic key)"}
        else:
            yield {"type": "no_results", "message": "No results. Add an Anthropic API key to enable smarter retries."}

    # ── Sort + log ────────────────────────────────────────────────────────────
    results = [r for r, _ in rank_results(results, quality, prefer_smaller)[:limit]]

    log.info("Search complete", extra={"query": query, "results": len(results), "winner": winner, "attempts": len(tried)})

    _log(data_dir, {
        "ts": time.time(), "query": query, "media_type": mt,
        "quality": quality.value, "total_results": len(results),
        "winner": winner, "attempts": len(tried), "evolved": winner == "cwm_evolved",
    })

    yield {"type": "done", "results": [r.to_dict() for r in results], "winner": winner, "attempts": len(tried)}


# ─── Search deeper stream ─────────────────────────────────────────────────────

async def search_deeper_stream(
    query: str,
    existing_hashes: list[str],
    media_type: Optional[MediaType] = None,
    quality: Quality = Quality.Q_1080P,
    limit: int = 20,
    data_dir: str = "./data",
    anthropic_api_key: str = "",
) -> AsyncIterator[dict]:
    """
    Ask Claude to find alternative results when user wants more options.
    Excludes hashes already shown. Same event types as search_stream.
    """
    log.info("Deeper search requested", extra={"query": query, "existing": len(existing_hashes)})

    if not anthropic_api_key:
        yield {"type": "no_results", "message": "Add an Anthropic API key to find alternatives."}
        yield {"type": "done", "results": [], "winner": None, "attempts": 0}
        return

    cat = "0"
    if media_type == MediaType.TV:
        cat = "208" if quality in (Quality.Q_1080P, Quality.Q_2160P) else "500"
    elif media_type == MediaType.MOVIE:
        cat = "207" if quality in (Quality.Q_1080P, Quality.Q_2160P) else "200"

    yield {"type": "evolving", "message": f"Asking Claude to find alternatives for '{query}'..."}
    await asyncio.sleep(0)

    new_queries: list[str] = []
    async for ev in _suggest_alternatives_stream(query, quality.value, anthropic_api_key):
        if ev["type"] == "_result":
            new_queries = ev.get("queries", [])
        else:
            yield ev

    if not new_queries:
        yield {"type": "evolving_failed", "message": "Could not generate alternatives (check Anthropic key)"}
        yield {"type": "done", "results": [], "winner": None, "attempts": 0}
        return

    exclude = {h.lower() for h in existing_hashes}
    results: list[SearchResult] = []
    winner: Optional[str] = None
    tried: list[str] = []

    for q in new_queries:
        if q.lower() in [x.lower() for x in tried]:
            continue
        tried.append(q)
        yield {"type": "trying", "strategy": "alternatives", "query": q, "attempt": len(tried)}
        await asyncio.sleep(0)

        raw: list[dict] = []
        async for ev in _query_with_pings(q, cat):
            if ev["type"] == "_result":
                raw = ev["raw"]
            else:
                yield ev

        new_raw = [r for r in raw if r.get("info_hash", "").lower() not in exclude]
        if new_raw:
            new_results = list({r["info_hash"]: _to_result(r) for r in new_raw}.values())
            results.extend(new_results)
            # Deduplicate across iterations
            seen_hashes: set[str] = set()
            deduped = []
            for r in results:
                if r.info_hash not in seen_hashes:
                    seen_hashes.add(r.info_hash)
                    deduped.append(r)
            results = deduped
            winner = "alternatives"
            yield {"type": "hit", "strategy": "alternatives", "query": q, "count": len(new_results)}
        else:
            yield {"type": "miss", "strategy": "alternatives", "query": q}

    results = [r for r, _ in rank_results(results, quality, False)[:limit]]

    log.info("Deeper search complete", extra={"query": query, "results": len(results), "attempts": len(tried)})
    yield {"type": "done", "results": [r.to_dict() for r in results], "winner": winner, "attempts": len(tried)}


# ─── Single-episode search ───────────────────────────────────────────────────

def _episode_queries(title: str, season: int, episode: int, quality: str) -> list[str]:
    tag = f"S{season:02d}E{episode:02d}"
    queries = [
        f"{title} {tag} {quality}",
        f"{title} {tag}",
        f"{title} {season}x{episode:02d}",
    ]
    if title.lower().startswith("the "):
        queries.append(f"{title[4:]} {tag}")
    return queries


async def search_episode_torrent(
    title: str,
    season: int,
    episode: int,
    quality: Quality = Quality.Q_1080P,
    data_dir: str = "./data",
    cat: str = "208",
) -> Optional[SearchResult]:
    """Find the best available torrent for a single TV episode."""
    queries = _episode_queries(title, season, episode, quality.value)
    seen: set[str] = set()
    for q in queries:
        if q.lower() in seen:
            continue
        seen.add(q.lower())
        raw = await _query(q, cat)
        if raw:
            results = list({r["info_hash"]: _to_result(r) for r in raw}.values())
            return sorted(results, key=lambda r: -r.seeders)[0]
    return None


async def search_episode_torrents(
    title: str,
    season: int,
    episode: int,
    quality: Quality = Quality.Q_1080P,
    data_dir: str = "./data",
    cat: str = "208",
    limit: int = 5,
) -> list[SearchResult]:
    """Find ranked candidate torrents for a single TV episode."""
    queries = _episode_queries(title, season, episode, quality.value)
    seen_queries: set[str] = set()
    by_hash: dict[str, SearchResult] = {}
    for q in queries:
        ql = q.lower()
        if ql in seen_queries:
            continue
        seen_queries.add(ql)
        for raw in await _query(q, cat):
            result = _to_result(raw)
            by_hash[result.info_hash.lower()] = result
    ranked = rank_results(list(by_hash.values()), quality)
    return [result for result, _ in ranked[:limit]]


async def search_season_pack_torrents(
    title: str,
    season: int,
    quality: Quality = Quality.Q_1080P,
    data_dir: str = "./data",
    cat: str = "208",
    limit: int = 12,
    prefer_smaller: bool = False,
) -> list[SearchResult]:
    """Find candidate full-season torrents before falling back to episode search."""
    s_tag = f"S{season:02d}"
    quality_value = quality.value if isinstance(quality, Quality) else str(quality)
    queries = [
        f"{title} {s_tag} {quality_value}",
        f"{title} {s_tag} complete {quality_value}",
        f"{title} season {season} complete {quality_value}",
        f"{title} season {season} {quality_value}",
    ]
    if prefer_smaller:
        queries = [q + " x265" for q in queries[:2]] + [q + " HEVC" for q in queries[:2]] + queries
    if title.lower().startswith("the "):
        bare = title[4:]
        queries += [q.replace(title, bare, 1) for q in queries[:4]]

    seen_queries: set[str] = set()
    by_hash: dict[str, SearchResult] = {}
    for q in queries:
        ql = q.lower()
        if ql in seen_queries:
            continue
        seen_queries.add(ql)
        for raw in await _query(q, cat):
            result = _to_result(raw)
            if _looks_like_season_pack(result.name, season):
                by_hash[result.info_hash.lower()] = result
    ranked = rank_results(list(by_hash.values()), quality, prefer_smaller, season)
    return [r for r, _ in ranked[:limit]]


# ─── Non-streaming wrapper ────────────────────────────────────────────────────

async def search(
    query: str,
    media_type: Optional[MediaType] = None,
    quality: Quality = Quality.Q_1080P,
    limit: int = 30,
    data_dir: str = "./data",
    tmdb_api_key: str = "",
    anthropic_api_key: str = "",
) -> list[SearchResult]:
    results: list[SearchResult] = []
    async for event in search_stream(query, media_type, quality, limit, data_dir, tmdb_api_key, anthropic_api_key):
        if event["type"] == "done":
            results = [SearchResult(**{
                k: v for k, v in r.items()
                if k in SearchResult.__dataclass_fields__
            }) for r in event["results"]]
    return results
