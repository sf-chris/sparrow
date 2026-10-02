"""Which episode a release or file name is: shared by acquisition and subtitle search."""

from __future__ import annotations

import re


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


ROMAN = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}


def _plain(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def sequel_of(name: str, titles, season: int) -> bool:
    """A release of a sequel season named after the title ("Show III - 02",
    "Show 2 - 05", "Show Second Season") when another season is wanted."""
    plain = _plain(name)
    for title in titles:
        base = _plain(title)
        if not base or base not in plain:
            continue
        rest = plain.split(base, 1)[1].split()
        if not rest:
            continue
        marker = rest[0]
        number = ROMAN.get(marker) or (int(marker) if marker.isdigit() and len(marker) == 1 else None)
        if number is None and len(rest) > 1 and rest[1] == "season":
            number = {"second": 2, "third": 3, "fourth": 4, "2nd": 2, "3rd": 3, "4th": 4}.get(marker)
        if number is not None and number != season:
            return True
    return False
