"""Subtitle tracks extracted from anime releases, found by episode (Anime Tosho).

Fan and official-rip releases carry their English subtitles as tracks; the
archive extracts and serves each one. When a downloaded copy has no usable
English text, a track from another release of the same episode is the next
best thing to the release's own: it is measured against this copy's
dialogue, corrected and verified like any other human-made track. Keyless;
a handful of requests per title.
"""

from __future__ import annotations

import lzma
import re

import httpx

from .subtitle_node import track_kind

FEED = "https://feed.animetosho.org/json"
STORAGE = "https://storage.animetosho.org/attach/{id:08x}/subtitle.{ext}.xz"
TEXT_CODECS = {"ASS": "ass", "SSA": "ass", "SRT": "srt", "UTF8": "srt", "SUBRIP": "srt"}
ENGLISH = {"eng", "en", "enm", "en-us", "en-gb"}
MAX_RELEASES = 4
MAX_BYTES = 16 * 1024 * 1024
HEADERS = {"User-Agent": "Sparrow subtitle finder (personal media library)"}


def episode_in(filename: str, season: int, episode: int) -> bool:
    """Whether a release file is this episode: S01E02, 1x02 or an absolute
    " - 02" / " 02 " number (season one only, as fansubs number seasons apart)."""
    name = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", filename)
    tagged = re.search(r"(?i)\bS(\d{1,2})\s*E(\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b", name)
    if tagged:
        found_season = int(tagged.group(1) or tagged.group(3))
        found_episode = int(tagged.group(2) or tagged.group(4))
        return (found_season, found_episode) == (season, episode)
    if season != 1:
        return False
    numbers = re.findall(r"(?:^|[\s_\-.])(\d{1,3})(?:v\d)?(?=[\s_\-.]|$)", name.rsplit(".", 1)[0])
    return any(int(n) == episode for n in numbers if not 1900 <= int(n) <= 2100)


SEASON_TAG = re.compile(r"(?i)\bS(\d{1,2})\b(?!\s*E\d)|\bseason\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+season\b")


def other_season(title: str, season: int) -> bool:
    """A release that names a different season (fansubs often restart
    episode numbers each season)."""
    return any(int(next(g for g in m.groups() if g)) != season for m in SEASON_TAG.finditer(title or ""))


def _english(info: dict) -> bool:
    language = str(info.get("lang") or "").lower()
    return language in ENGLISH or (language in ("", "und") and "english" in str(info.get("name") or "").lower())


def _plain(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


async def search(
    titles: list[str], season: int, episode: int, *, exclude: list[str] = (), client: httpx.AsyncClient | None = None
) -> list[dict]:
    """English text subtitle tracks for one episode, best releases first.

    exclude: titles of other seasons (TMDB lists them), whose releases restart
    episode numbers and must not stand in for this one.
    """
    excluded = [_plain(t) for t in exclude if len(_plain(t)) >= 6]
    own = client is None
    client = client or httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True)
    try:
        releases, seen = [], set()
        for title in [t for t in titles if t][:3]:
            for query in (f"{title} {episode:02d}", f"{title} S{season:02d}E{episode:02d}"):
                response = await client.get(FEED, params={"q": query})
                if response.status_code != 200:
                    continue
                for row in response.json() or []:
                    if row.get("id") in seen or row.get("status") != "complete" or other_season(row.get("title"), season):
                        continue
                    if any(name in _plain(row.get("title")) for name in excluded):
                        continue
                    seen.add(row["id"])
                    releases.append(row)
            if len(releases) >= MAX_RELEASES:
                break
        # Single-episode releases first: their file is the episode itself.
        releases.sort(key=lambda r: (not episode_in(r.get("title") or "", season, episode), r.get("num_files") or 99))
        candidates, attachments = [], set()
        for release in releases[:MAX_RELEASES]:
            response = await client.get(FEED, params={"show": "torrent", "id": release["id"]})
            if response.status_code != 200:
                continue
            for file in (response.json() or {}).get("files") or []:
                if not episode_in(file.get("filename") or "", season, episode):
                    continue
                for attachment in file.get("attachments") or []:
                    info = attachment.get("info") or {}
                    codec = str(info.get("codec") or "").upper()
                    if attachment.get("type") != "subtitle" or codec not in TEXT_CODECS or not _english(info):
                        continue
                    if attachment["id"] in attachments:
                        continue  # the same file reposted in another release
                    attachments.add(attachment["id"])
                    name = str(info.get("name") or "")
                    candidates.append(
                        {
                            "id": f"archive:{attachment['id']}",
                            "source": "archive",
                            "attachment": int(attachment["id"]),
                            "format": TEXT_CODECS[codec],
                            "language": "en",
                            "kind": track_kind({"title": name, "forced": bool(info.get("forced")), "hearing_impaired": False}),
                            "title": f"{(release.get('title') or '')[:80]} · {name or 'English'}",
                            # Larger tracks carry more dialogue; honorific variants after plain ones.
                            "cue_count": int(attachment.get("size") or 0) // 80 - (1 if str(info.get("lang")).lower() == "enm" else 0),
                        }
                    )
        return candidates
    finally:
        if own:
            await client.aclose()


async def fetch(candidate: dict, *, client: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """The track's text and format."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=60, headers=HEADERS, follow_redirects=True)
    try:
        response = await client.get(STORAGE.format(id=candidate["attachment"], ext=candidate["format"]))
        response.raise_for_status()
        raw = lzma.decompress(response.content, memlimit=MAX_BYTES * 4)
        if len(raw) > MAX_BYTES:
            raise ValueError("This archived subtitle is too large.")
        return raw.decode("utf-8-sig", "replace"), candidate["format"]
    finally:
        if own:
            await client.aclose()
