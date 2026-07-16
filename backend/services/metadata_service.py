"""
Metadata service — fetches rich media metadata and artwork from TMDB.

All images are cached locally so the library works offline after first fetch.
TMDB API is free: https://www.themoviedb.org/settings/api
"""
from __future__ import annotations
import re
import urllib.parse
from typing import Optional
from pathlib import Path
import httpx

from ..models import MediaType


TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"


async def tmdb_quick_suggest(query: str, api_key: str) -> list[dict]:
    """
    Lightweight /search/multi call for the suggestion dropdown.
    Returns slim objects — no image downloading, no full details fetch.
    """
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{TMDB_BASE}/search/multi",
                params={"api_key": api_key, "query": query, "include_adult": "false", "page": "1"},
            )
            items = []
            for r in resp.json().get("results", [])[:8]:
                mt = r.get("media_type")
                if mt not in ("movie", "tv"):
                    continue
                title = r.get("title") or r.get("name", "")
                year = (r.get("release_date") or r.get("first_air_date") or "")[:4]
                poster = r.get("poster_path")
                items.append({
                    "tmdb_id": r["id"],
                    "title": title,
                    "media_type": mt,
                    "year": year,
                    "poster_url": f"{TMDB_IMAGE_BASE}/w342{poster}" if poster else None,
                    "rating": round(float(r.get("vote_average") or 0), 1),
                    "overview": (r.get("overview") or "")[:120],
                })
            return items
    except Exception:
        return []


async def tmdb_tv_seasons(tmdb_id: int, api_key: str) -> list[dict]:
    """Return the season list for a TV show (skips season 0 / specials)."""
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{TMDB_BASE}/tv/{tmdb_id}",
                params={"api_key": api_key},
            )
            seasons = []
            for s in resp.json().get("seasons", []):
                if s.get("season_number", 0) == 0:
                    continue
                seasons.append({
                    "season_number": s["season_number"],
                    "episode_count": s.get("episode_count", 0),
                    "name": s.get("name", f"Season {s['season_number']}"),
                })
            return seasons
    except Exception:
        return []


async def search_tmdb(query: str, media_type: MediaType, api_key: str, year: Optional[int] = None) -> list[dict]:
    """Search TMDB for movies or TV shows. For UNKNOWN type, uses /search/multi and picks best result."""
    if not api_key:
        return []

    async with httpx.AsyncClient(timeout=10.0) as client:
        if media_type == MediaType.UNKNOWN:
            # Multi-search picks up both movies and TV shows
            params: dict = {"api_key": api_key, "query": query, "include_adult": "false"}
            if year:
                params["year"] = str(year)
            try:
                resp = await client.get(f"{TMDB_BASE}/search/multi", params=params)
                results = [r for r in resp.json().get("results", []) if r.get("media_type") in ("movie", "tv")]
                # Annotate so callers can read back the resolved type
                return results
            except Exception:
                return []

        endpoint = "/search/movie" if media_type == MediaType.MOVIE else "/search/tv"
        params = {"api_key": api_key, "query": query, "include_adult": "false"}
        if year:
            key = "primary_release_year" if media_type == MediaType.MOVIE else "first_air_date_year"
            params[key] = str(year)

        try:
            resp = await client.get(f"{TMDB_BASE}{endpoint}", params=params)
            return resp.json().get("results", [])
        except Exception:
            return []


async def get_movie_details(tmdb_id: int, api_key: str) -> Optional[dict]:
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{TMDB_BASE}/movie/{tmdb_id}",
                params={"api_key": api_key, "append_to_response": "credits,external_ids"},
            )
            return resp.json()
    except Exception:
        return None


async def get_tv_details(tmdb_id: int, api_key: str) -> Optional[dict]:
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{TMDB_BASE}/tv/{tmdb_id}",
                params={"api_key": api_key, "append_to_response": "credits,external_ids"},
            )
            return resp.json()
    except Exception:
        return None


async def download_image(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
    except Exception:
        pass
    return None


def tmdb_image_url(path: str, size: str = "w500") -> str:
    return f"{TMDB_IMAGE_BASE}/{size}{path}"


def extract_year_from_name(name: str) -> Optional[int]:
    """Try to parse a year from a torrent name like 'The Matrix 1999 1080p'."""
    m = re.search(r"\b(19|20)\d{2}\b", name)
    if m:
        return int(m.group(0))
    return None


def clean_search_query(torrent_name: str) -> tuple[str, Optional[int], str]:
    """
    Turn a raw torrent name into a clean search query.
    Returns (clean_title, year, detected_quality).

    e.g. "The.Matrix.1999.1080p.BluRay.x264" -> ("The Matrix", 1999, "1080p")
    """
    name = torrent_name

    # Detect quality before stripping
    quality = "unknown"
    for q in ["2160p", "1080p", "720p", "480p"]:
        if q in name.lower():
            quality = q
            break

    # Replace dots/underscores with spaces
    name = re.sub(r"[._]", " ", name)

    # Extract year
    year = extract_year_from_name(name)

    # Strip everything from year onwards (or from quality marker onwards)
    cutoffs = [
        r"\b(19|20)\d{2}\b",
        r"\b(2160p|1080p|720p|480p|dvdrip|bluray|bdrip|webrip|web-dl|hdtv|xvid|x264|x265|hevc|aac|ac3|dts|remux)\b",
        r"\bS\d{2}E\d{2}\b",
        r"\bSeason\b",
    ]
    for pattern in cutoffs:
        m = re.search(pattern, name, re.IGNORECASE)
        if m:
            name = name[:m.start()].strip()
            break

    # Clean up leftover brackets/parens
    name = re.sub(r"[\[\](){}]", "", name).strip()
    name = re.sub(r"\s+", " ", name)

    return name, year, quality


async def fetch_metadata_for_torrent(
    torrent_name: str,
    media_type: MediaType,
    api_key: str,
    art_cache_dir: Path,
    tmdb_id: Optional[int] = None,
) -> dict:
    """
    Complete metadata pipeline for a torrent:
    1. Parse clean title + year from torrent name
    2. Search TMDB
    3. Fetch full details
    4. Download and cache poster + backdrop
    Returns a flat metadata dict.
    """
    clean_title, year, quality = clean_search_query(torrent_name)

    result = {
        "title": clean_title,
        "year": year,
        "quality": quality,
        "tmdb_id": tmdb_id,
        "overview": "",
        "poster_path": "",
        "backdrop_path": "",
        "genres": [],
        "rating": None,
        "imdb_id": None,
    }

    if not api_key:
        return result

    # Search if no TMDB id given
    if not tmdb_id:
        candidates = await search_tmdb(clean_title, media_type, api_key, year)
        if candidates:
            best = candidates[0]
            tmdb_id = best["id"]
            result["tmdb_id"] = tmdb_id
            # Multi-search results carry media_type; resolve UNKNOWN
            if media_type == MediaType.UNKNOWN and "media_type" in best:
                media_type = MediaType.MOVIE if best["media_type"] == "movie" else MediaType.TV

    if not tmdb_id:
        return result

    # Fetch full details
    if media_type == MediaType.MOVIE:
        details = await get_movie_details(tmdb_id, api_key)
    else:
        details = await get_tv_details(tmdb_id, api_key)

    if not details:
        return result

    # Extract fields
    if media_type == MediaType.MOVIE:
        result["title"] = details.get("title", clean_title)
        result["year"] = int(details.get("release_date", "")[:4] or year or 0) or None
        result["imdb_id"] = details.get("external_ids", {}).get("imdb_id") or details.get("imdb_id")
    else:
        result["title"] = details.get("name", clean_title)
        result["year"] = int(details.get("first_air_date", "")[:4] or year or 0) or None
        result["imdb_id"] = details.get("external_ids", {}).get("imdb_id")
        result["seasons"] = details.get("number_of_seasons")
        result["episode_count"] = details.get("number_of_episodes")

    result["overview"] = details.get("overview", "")
    result["rating"] = details.get("vote_average")
    result["genres"] = [g["name"] for g in details.get("genres", [])]

    # Download artwork
    poster_p = details.get("poster_path")
    backdrop_p = details.get("backdrop_path")

    if poster_p:
        img_data = await download_image(tmdb_image_url(poster_p, "w500"))
        if img_data:
            path = art_cache_dir / f"poster_{tmdb_id}.jpg"
            path.write_bytes(img_data)
            result["poster_path"] = f"/art/poster_{tmdb_id}.jpg"

    if backdrop_p:
        img_data = await download_image(tmdb_image_url(backdrop_p, "w780"))
        if img_data:
            path = art_cache_dir / f"backdrop_{tmdb_id}.jpg"
            path.write_bytes(img_data)
            result["backdrop_path"] = f"/art/backdrop_{tmdb_id}.jpg"

    return result
