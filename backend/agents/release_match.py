"""Which episode a release or file name is: shared by acquisition and subtitle search."""

from __future__ import annotations

import re


def episode_in(filename: str, season: int, episode: int) -> bool:
    """Whether a release file is this episode: S01E02, 1x02 or an absolute
    " - 02" / " 02 " number, in season one or under a tag naming the season
    ("Show S2 - 05", "Show Season 2 - 05"), as fansubs number seasons apart."""
    name = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", filename)
    tagged = re.search(r"(?i)\bS(\d{1,2})\s*E(\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b", name)
    if tagged:
        found_season = int(tagged.group(1) or tagged.group(3))
        found_episode = int(tagged.group(2) or tagged.group(4))
        return (found_season, found_episode) == (season, episode)
    seasons = {int(next(g for g in m.groups() if g)) for m in SEASON_TAG.finditer(name)}
    if (seasons and season not in seasons) or (not seasons and season != 1):
        return False
    # The episode number is the one marked as such (" - 14", "E14", "Ep 14"),
    # else the first standalone number: "part 2" or "Season 2" later in a
    # name is not episode 2.
    stem = SEASON_TAG.sub(" ", name).rsplit(".", 1)[0]
    marked = re.findall(r"(?i)(?:[\s_]-[\s_]+|\b(?:ep|episode|e)[\s._]?|#)(\d{1,4})(?:v\d)?(?![\d])", stem)
    numbers = marked or re.findall(r"(?:^|[\s_\-.])(\d{1,4})(?:v\d)?(?=[\s_\-.]|$)", stem)
    numbers = numbers or re.findall(r"\[(\d{1,4})(?:v\d)?\]", filename)  # "[Group][Show][02]"
    numbers = [int(n) for n in numbers if not 1900 <= int(n) <= 2100]
    return bool(numbers) and numbers[0] == episode


SEASON_TAG = re.compile(r"(?i)\bS(\d{1,2})\b(?!\s*E\d)|\bseason\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+season\b")


def other_season(title: str, season: int) -> bool:
    """A release that names a different season (fansubs often restart
    episode numbers each season)."""
    return any(int(next(g for g in m.groups() if g)) != season for m in SEASON_TAG.finditer(title or ""))


SEASON_RANGE = re.compile(r"(?i)\bS(\d{1,2})\s*[-~]\s*S?(\d{1,2})\b|\bseasons?\s*(\d{1,2})\s*(?:-|~|to|&|and)\s*(\d{1,2})\b")


def covers_season(title: str, season: int) -> bool:
    """A multi-season release that includes this season ("S01-S05",
    "Seasons 1 to 6", or a season tag naming it alongside others)."""
    for m in SEASON_RANGE.finditer(title or ""):
        low, high = (int(g) for g in (m.group(1, 2) if m.group(1) else m.group(3, 4)))
        if low <= season <= high:
            return True
    return any(int(next(g for g in m.groups() if g)) == season for m in SEASON_TAG.finditer(title or ""))


ROMAN = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}


def _plain(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def sequel_of(name: str, titles, season: int) -> bool:
    """A release of a sequel season named after the title ("Show III - 02",
    "Show 2 - 05", "Show Second Season") when another season is wanted.
    Punctuation and spacing are ignored ("Tensai-tachi" is "Tensaitachi")."""
    for title in titles:
        rest = _following(name, re.sub(r"[^a-z0-9]", "", str(title).lower()))
        if rest is None:
            continue
        rest = _plain(rest).split()
        if not rest:
            continue
        marker = rest[0]
        number = ROMAN.get(marker) or (int(marker) if marker.isdigit() and len(marker) == 1 else None)
        if number is None and len(rest) > 1 and rest[1] == "season":
            number = {"second": 2, "third": 3, "fourth": 4, "2nd": 2, "3rd": 3, "4th": 4}.get(marker)
        if number is not None and number != season:
            return True
    return False


def _following(name: str, compact: str):
    """The text after a title found anywhere in a name, letters and digits
    compared; None when the name doesn't contain it."""
    if not compact:
        return None
    chars = [(i, c.lower()) for i, c in enumerate(str(name)) if c.isascii() and c.isalnum()]
    letters = "".join(c for _, c in chars)
    at = letters.find(compact)
    if at < 0:
        return None
    return str(name)[chars[at + len(compact) - 1][0] + 1:]
