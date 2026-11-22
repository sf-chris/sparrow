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
import unicodedata
import urllib.parse

import httpx

from ..services.release_parser import parse_release_name
from .release_match import covers_season, episode_in, numbered, other_season, sequel_of
from .errors import ToolError

QUALITY_RANK = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4}
ROWS = 8
ENOUGH = 4  # healthy candidates that end the search early…
WELL_SEEDED = 20  # …once one of them has a swarm like this
PEEKS = 4
WAIT = 900  # seconds to queue for a search slot while nothing usable is found yet
SHORT_WAIT = 60  # and once something usable is
DUB_ONLY = re.compile(r"(?i)\b(eng(lish)?[\s._-]?dub(bed)?|dubbed|dub([\s._-]?only)?)\b")
DUAL = re.compile(r"(?i)\b(dual[\s._-]?audio|multi[\s._-]?audio|jap(anese)?|jpn|original[\s._-]?audio)\b")
SUBS = re.compile(r"(?i)\b(multi[\s._-]?subs?|e?subs?|eng[\s._-]?subs?|subbed|softsubs?|cr|nf|amzn|dsnp|hidive|web[\s._-]?dl)\b")
RAW = re.compile(r"(?i)(?:\b|_)raws?(?:\b|_)")  # untranslated: no subtitles
ALTERNATE = re.compile(r"(?i)\b(dc|director'?s[\s._-]?cut|extended|uncut|alt(ernate)?)\b")
VIDEO = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm")


def _plain(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


NOISE = re.compile(r"^(\s*(\[[^\]]*\]|\([^)]*\)|www\.\S+\s*-|[-_.\s]))+", re.I)
# What may follow the title in a release of it: numbering, years, seasons,
# packaging and quality tags. Any other word means another show or part
# ("Naruto Shippuden", "Tokyo Ghoul Root A", "Monster The Ed Gein Story").
FOLLOWS = re.compile(
    r"(?i)^[\s._\-:~,!?'&+]*(?:$|[\[({]|(?:"
    r"s\d{1,2}(?:\s*e\d{1,4})?|e\d{1,4}|\d{1,2}x\d{1,3}|\d{1,4}(?:v\d|st|nd|rd|th)?|v\d|"
    r"seasons?|series|complete|the\s+complete|batch|collection|ep|eps|episodes?|vol|volume|part|ova|oad|specials?|"
    r"bd|bdrip|bluray|blu|web|webrip|hdtv|dvd|dvdrip|remux|uhd|hdr|4k|\d{3,4}p|"
    r"dual|multi|eng|english|sub|subs|subbed|dub|dubbed|jap|japanese|jpn|vostfr|raw|uncensored|uncut|remastered|"
    r"us|uk|au|nz|ca|tv|integrale|temporada|staffel|saison)(?=$|[\W_]))"
)


def compact(text):
    """Letters and digits only, accents folded: "Shippūden" is "shippuden"."""
    return re.sub(r"[^a-z0-9]", "", _fold(text).lower())


LOOKALIKES = str.maketrans({"×": "x", "～": "~", "’": "'", "–": "-", "—": "-"})


def _fold(text):
    folded = unicodedata.normalize("NFKD", str(text).translate(LOOKALIKES))
    return folded.encode("ascii", "ignore").decode()


def latin(text):
    """Whether a name is in Latin script (accents allowed), as the index
    names releases; other scripts reduce to stray digits when compared."""
    letters = [c for c in unicodedata.normalize("NFKD", str(text)) if c.isalpha()]
    return len(letters) >= 3 and all(c.isascii() for c in letters)


def searchable(text):
    """A title as the index can search it: every word must appear in a name,
    punctuation matches nothing and "-word" excludes a word."""
    return " ".join(re.sub(r"[^\w'\-]+|(?<!\w)-+|-+(?!\w)", " ", _fold(text)).split())


def main_part(title):
    """A title without its subtitle ("Kaguya-sama: Love Is War" is
    "Kaguya-sama"), or "" when it has none worth searching by."""
    part = re.split(r":\s|\s[-–~]", str(title), maxsplit=1)[0]
    return part if len(compact(part)) >= 5 and compact(part) != compact(title) else ""


def _after(name, title):
    """What follows a title the name opens with, or None when it doesn't."""
    body, matched = _fold(NOISE.sub("", name)), 0
    for i, char in enumerate(body):
        if not char.isalnum():
            continue
        if matched == len(title) or char.lower() != title[matched]:
            return None
        matched += 1
        if matched == len(title):
            return body[i + 1:]
    return None


CAPPED = 50  # rows (after dead swarms are dropped) that suggest the index's cap of 100


def blockers(rows, titles, most=3):
    """The words that most often follow this title in names of other shows
    ("Shippuden" after "Naruto"), for excluding them from a search."""
    forms = {compact(form) for title in titles for form in (title, main_part(title)) if form}
    counts = {}
    for row in rows:
        name = row.get("name") or ""
        if titled(name, titles):
            continue
        rests = [rest for rest in (_after(name, form) for form in sorted(filter(None, forms), key=len, reverse=True)) if rest is not None]
        word = re.match(r"[\s._\-:~,!?'&+]*([A-Za-z][A-Za-z']{2,})", rests[0]) if rests else None
        if word:
            counts[word.group(1).lower()] = counts.get(word.group(1).lower(), 0) + 1
    return [w for w, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n >= 3][:most]


def titled(name, titles):
    """Whether a release name is this show: it opens with one of its titles
    (or a title without its subtitle) followed only by numbering, year,
    season and quality tags. "LEGO ONE PIECE" and "Naruto Shippuden" are not."""
    forms = {compact(form) for title in titles for form in (title, main_part(title)) if form}
    for form in filter(None, forms):
        rest = _after(name, form)
        if rest is not None and FOLLOWS.match(rest):
            return True
    return not forms


def _rank(quality):
    return QUALITY_RANK.get(quality, 0)


def queries(titles, targets, media_type, year=None):
    """The searches a careful person would try first, in the index's terms:
    scene names (Title S01E02), packs (Title, Title Year), then the romanised
    title fansubs use (Romaji - 02)."""
    season = targets[0][0] if targets else 1
    # An alternative's subtitle rarely appears in release names.
    first, *rest = [searchable(t if n == 0 else main_part(t) or t) for n, t in enumerate(titles[:2])] or [""]
    second = rest[0] if rest else ""
    if media_type == "movie":
        out = [f"{first} {year}" if year else first, first]
        out += [f"{second} {year}" if year else second] if second else []
    else:
        few = len(targets) <= 2
        tagged = [f"S{s:02d}E{e:02d}" for s, e in targets] if few else []
        absolute = [f"{e:02d}" for s, e in targets] if few and season == 1 else []
        out = [f"{first} {tag}" for tag in tagged] + [first]
        if second:
            out += [f"{second} {tag}" for tag in tagged + absolute]
        out += [f"{first} {year}"] if year else []
        out += [f"{first} complete"]  # series and season packs
        out += [f"{first} {tag}" for tag in absolute] + [f"{first} S{season:02d}"]
        if second:
            out += [second, f"{second} S{season:02d}"]
    unique = {}
    for query in (q.strip() for q in out if q.strip()):
        unique.setdefault(query.lower(), query)  # the index ignores case
    return list(unique.values())


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
    if parsed.risk_flags or not titled(name, titles):
        return None
    if _rank(parsed.quality) and _rank(parsed.quality) < _rank(job.min_quality):
        return None
    if job.audio_pref == "original" and DUB_ONLY.search(name) and not DUAL.search(name):
        return None
    if job.year and parsed.year and (
        abs(parsed.year - job.year) > 1 if job.media_type == "movie" else season == 1 and parsed.year != job.year
    ):
        return None  # another film, a remake or a same-named series
    if excluded(name, exclude):
        return None  # another season's own title, or a same-named show's episode
    if job.media_type != "movie" and EPISODE_TAG.search(name) and not EPISODE_RANGE.search(name):
        if not any(episode_in(name, s, e) for s, e in targets):
            return None  # one episode, and not a wanted one
    if job.media_type == "movie":
        coverage = "single"
    elif (other_season(name, season) or sequel_of(name, titles, season)) and not covers_season(name, season):
        return None
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
        "subs": bool(SUBS.search(name)) and not RAW.search(name),
        "raw": bool(RAW.search(name)),
        "complete": bool(parsed.is_complete_series or parsed.is_season_pack),
        "year": parsed.year,
        # Signals that the name may be another season, a sequel or another cut.
        "other_part": bool(other_season(name, season) or sequel_of(name, titles, season) or ALTERNATE.search(name))
        if job.media_type != "movie" else bool(ALTERNATE.search(name)),
    }


EPISODE_TAG = re.compile(r"(?i)\bS\d{1,2}\s*E\d{1,4}\b|\b\d{1,2}x\d{2,3}\b")
EPISODE_RANGE = re.compile(r"(?i)\bS\d{1,2}\s*E\d{1,4}\s*[-~]\s*(?:S\d{1,2})?\s*E?\d{1,4}\b")


def excluded(name, exclude):
    """Whether a name contains one of the names to exclude (Latin ones only:
    other scripts reduce to stray digits that every name contains)."""
    plain = _plain(name)
    return any(latin(other) and len(compact(other)) >= 6 and _plain(other) in plain for other in exclude)


def spans(candidate, targets):
    """Whether an unlisted pack's name covers the wanted episodes: a whole
    series or season, or a numbered range such as (01-26)."""
    if candidate.get("complete"):
        return True
    ranges = [(int(a), int(b)) for a, b in re.findall(r"(?<!\d)(\d{1,3})\s*[-~]\s*(\d{1,3})(?!\d)", candidate["name"])]
    return bool(ranges) and all(any(a <= e <= b for a, b in ranges) for s, e in targets if s == 1) and all(s == 1 for s, _ in targets)


def _wants_subtitles(job):
    return "en" in (((job.preferences or {}).get("values") or {}).get("subtitle_languages") or [])


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
        - (1 if candidate.get("unlisted") else 0)  # its files are a promise until it starts
        - (2 if candidate.get("raw") and _wants_subtitles(job) else 0)
        - (4 if candidate.get("disputed") else 0)  # its number may be another episode
    )


EXTRAS_FOLDER = re.compile(r"(?i)^[\W_]*(?:specials?|extras?|bonus|ova|oad|movies?|films?|ncop|nced|creditless|trailers?|pv|menus?)\b")
EXTRAS_FILE = re.compile(r"(?i)(?:^|[\W_])(?:ncop|nced|creditless|trailer|preview|menu|pv\d*)(?:[\W_]|$)")


def _extras(path):
    """A folder that holds extras ("Movies/", "Specials/") or an opening,
    ending or trailer file. A pack named "Show + Movies" is not one."""
    *folders, base = str(path).split("/")
    return any(EXTRAS_FOLDER.match(NOISE.sub("", part)) for part in [*folders, base]) or bool(EXTRAS_FILE.search(base))


def _other_part(path, titles, season, exclude):
    """A pack file that belongs to another season or show: a folder or file
    named for it ("Tokyo Ghoul Root A/...", "Season 2/...")."""
    if other_season(path, season) and not covers_season(path, season):
        return True
    if excluded(path, exclude) or _extras(path):
        return True
    forms = {compact(form) for title in titles for form in (title, main_part(title)) if form}
    for segment in str(path).split("/"):
        opening = compact(NOISE.sub("", segment))
        if any(form and opening.startswith(form) for form in forms) and not titled(segment, titles):
            return True
    return False


async def listing(apibay_id):
    """A torrent's files as the indexer lists them, as (name, size) pairs:
    empty when it has no list. The search benchmark replays this."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"https://apibay.org/f.php?id={urllib.parse.quote(apibay_id)}")
        data = response.json()
    from .tools import _apibay_indexed_value as value

    entries = []
    for entry in data if isinstance(data, list) else []:
        if isinstance(entry, dict):
            name, size = value(entry.get("name"), ""), value(entry.get("size"), 0)
            if name and name != "Filelist not found":
                entries.append((str(name), int(size or 0)))
    return entries


async def peek(apibay_id, targets, media_type, titles=(), exclude=(), names=None, letters=0, evidence=None):
    """The wanted episodes' files inside a pack ({(season, episode): file}):
    empty when none are there, None when the indexer cannot list it.
    names/letters: TMDB's episode names, matched against the file names
    (match()); how each wanted episode was found is appended to evidence."""
    entries = await listing(apibay_id)
    if not entries:
        return None
    chosen, found = match(entries, targets, media_type, titles, exclude, names, letters)
    if evidence is not None:
        evidence.extend(found)
    return chosen


UNLISTED = "episode:"  # a pack file chosen by episode once the real list is known


SUBTITLE_FILES = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup")


def resolve(files, wanted, titles=()):
    """The files to download, from a torrent's real list: names for
    "episode:S01E02" placeholders (a lone video stands in for an oddly named
    single release; a placeholder with no match stays, so the selection
    fails), plus the subtitle files that go with what was chosen."""
    targets = []
    for want in wanted:
        tag = re.fullmatch(r"episode:S(\d{1,2})E(\d{1,4})", str(want))
        if tag:
            targets.append((int(tag.group(1)), int(tag.group(2))))
    entries = [(str(f.get("name", "")), int(f.get("size") or 0)) for f in files]
    names = [w for w in wanted if not str(w).startswith(UNLISTED)]
    if targets:
        chosen = choose(entries, targets, "tv", titles)
        names += [f["name"] for f in chosen.values()]
        videos = [n for n, _ in entries if n.lower().endswith(VIDEO) and "sample" not in n.lower()]
        missing = [(s, e) for s, e in targets if (s, e) not in chosen]
        if missing and not chosen and not names and len(videos) == 1:
            names, missing = videos, []
        names += [f"{UNLISTED}S{s:02d}E{e:02d}" for s, e in missing]
    # Subtitle files beside the chosen videos are often the best English track.
    season = targets[0][0] if targets else 1
    by_episode = _by_episode(targets)
    stems = {_plain(n.rsplit("/", 1)[-1].rsplit(".", 1)[0]) for n in names if not n.startswith(UNLISTED)}
    for name, _ in entries:
        base = name.rsplit("/", 1)[-1]
        if not base.lower().endswith(SUBTITLE_FILES) or name in names:
            continue
        stem = _plain(base.rsplit(".", 1)[0])
        if any(s and stem.startswith(s) for s in stems) or (
            any(episode_in(base, *t) for t in _possible(base, by_episode)) and not _other_part(name, titles, season, ())
        ):
            names.append(name)
    return names


def _by_episode(targets):
    by_episode = {}
    for target in targets:
        by_episode.setdefault(target[1], []).append(target)
    return by_episode


def _possible(base, by_episode):
    """The targets a file name could be: its episode number is one of the
    numbers in it, so a long series' pack is not matched name by target."""
    return [t for n in dict.fromkeys(int(d) for d in re.findall(r"\d+", base)) for t in by_episode.get(n, ())]


def choose(entries, targets, media_type, titles=(), exclude=()):
    """The wanted episodes' files among (name, size) entries."""
    return {target: _best(files) for target, files in _candidates(entries, targets, media_type, titles, exclude).items()}


def _videos(entries, media_type, titles, season, exclude):
    """The (name, size) video files that could be a wanted item: no samples,
    and for TV nothing from another season, show or extras folder."""
    return [(name, size) for name, size in entries
            if name.lower().endswith(VIDEO) and "sample" not in name.lower()
            and (media_type == "movie" or not _other_part(name, titles, season, exclude))]


def _candidates(entries, targets, media_type, titles=(), exclude=()):
    """{target: [file]}: every file whose name numbers it as a wanted episode."""
    season = targets[0][0] if targets else 1
    by_episode = _by_episode(targets)
    found = {}
    for name, size in _videos(entries, media_type, titles, season, exclude):
        base = name.rsplit("/", 1)[-1]
        for target in ([None] if media_type == "movie" else _possible(base, by_episode)):
            if target is None or episode_in(base, *target):
                found.setdefault(target, []).append({"name": name, "size": size})
    return found


def _best(files):
    """The standard cut over alternates, then the largest: not a sample."""
    return max(files, key=lambda f: (not ALTERNATE.search(f["name"]), f["size"]))


# ─── Episode order: TMDB's episode titles against a pack's real file names ──

GENERIC_TITLE = re.compile(r"^(?:episode|ep|part|chapter|vol|volume|special|pilot|untitled|tba|tbd|final|recap)?(?: ?\d+)*$")


def title_words(text):
    """A name's words as compared with an episode title: accents folded,
    apostrophes dropped, any other punctuation a break."""
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"['’`]", "", _fold(text).lower())).split()


def _holds(words, part):
    """Whether part's words appear in words, together and in order."""
    n = len(part)
    return bool(n) and any(words[i:i + n] == part for i in range(len(words) - n + 1))


def matchable(names, titles=(), letters=8):
    """{(season, episode): words} of TMDB episode titles specific enough to
    find in a file name: at least `letters` letters, not "Episode 5", not
    part of the show's own title, and not inside another title that season.
    letters 0 matches nothing."""
    if not letters:
        return {}
    shows = [title_words(t) for t in titles if t]
    words = {key: title_words(name) for key, name in (names or {}).items() if name}
    out = {}
    for key, mine in words.items():
        if sum(len(w) for w in mine) < letters or GENERIC_TITLE.match(" ".join(mine)):
            continue
        if any(_holds(show, mine) for show in shows):
            continue
        if any(other != key and other[0] == key[0] and _holds(theirs, mine) for other, theirs in words.items()):
            continue  # "Jupiter Jazz" inside "Jupiter Jazz (Part 1)", or two episodes named alike
        out[key] = mine
    return out


def _tag(target):
    return f"S{target[0]:02d}E{target[1]:02d}"


def match(entries, targets, media_type, titles=(), exclude=(), names=None, letters=0):
    """The wanted episodes' files in a real file list, and how each was found.

    A file is first found by number, as choose() does. Where TMDB's title for
    a wanted episode is distinctive (matchable) the list is also read by
    title: a file carrying that title is the episode, whatever number the pack
    gives it (packs and TMDB often order a season differently), and a file
    numbered as the episode that carries another episode's title is not it.
    Returns ({target: file}, evidence): one evidence entry per wanted episode,
    {"target", "how", "file", "numbered", "title", "candidates"}, where how is
    "title_agrees", "title" (found by title under another number),
    "contradicted" (the numbered file is another episode), "number" or "none"."""
    found = _candidates(entries, targets, media_type, titles, exclude)
    chosen = {target: _best(files) for target, files in found.items()}
    if media_type == "movie":
        return chosen, []
    season = targets[0][0] if targets else 1
    distinct = matchable(names, titles, letters)
    videos = _videos(entries, media_type, titles, season, exclude) if distinct else []
    words = {name: title_words(name.rsplit("/", 1)[-1]) for name, _ in videos}
    by_title = {}
    for target in targets:
        if target in distinct:
            carrying = [{"name": n, "size": s} for n, s in videos if _holds(words[n], distinct[target])]
            if carrying:
                by_title[target] = _best(carrying)
    claimed = {file["name"]: target for target, file in by_title.items()}
    evidence = []
    for target in targets:
        numbered_file = chosen.get(target)
        entry = {"target": _tag(target), "title": (names or {}).get(target, ""),
                 "candidates": sum(1 for f in found.get(target, []) if not ALTERNATE.search(f["name"]))}
        if target in by_title:
            file = by_title[target]
            how = "title_agrees" if numbered_file and numbered_file["name"] == file["name"] else "title"
            chosen[target] = file
            entry.update(how=how, file=file["name"].rsplit("/", 1)[-1][:160], numbered=numbered(file["name"].rsplit("/", 1)[-1]))
        elif numbered_file:
            base = numbered_file["name"].rsplit("/", 1)[-1]
            other = claimed.get(numbered_file["name"]) or next(
                (key for key, title in distinct.items() if key != target and _holds(words.get(numbered_file["name"], []), title)), None)
            entry.update(how="contradicted" if other else "number", file=base[:160], numbered=numbered(base))
            if other:
                # Its own title says it is another episode: not this one.
                entry.update(is_episode=_tag(other), is_title=(names or {}).get(other, ""))
                del chosen[target]
        else:
            entry.update(how="none", file="", numbered="")
        evidence.append(entry)
    return chosen, evidence


async def episode_names(tb, job, targets):
    """TMDB's names for every episode of the wanted seasons, {(season, episode): name}."""
    names = {}
    for season in sorted({s for s, _ in targets}):
        try:
            data = await tb.tmdb_get(f"/tv/{job.tmdb_id}/season/{season}")
        except Exception:
            continue
        for e in data.get("episodes") or []:
            if isinstance(e.get("episode_number"), int):
                names[(season, e["episode_number"])] = e.get("name") or ""
    return names


def _disputes(rows, targets, names, titles, letters):
    """Mark copies whose wanted episode is chosen by number only, when another
    listing showed that number is not the episode (found by title under
    another number, or the numbered file carried another episode's title).
    A single release whose own name carries another episode's title is
    marked too. Names only ever lower confidence here."""
    distinct = matchable(names, titles, letters)
    disputed = {e["target"] for row in rows for e in row.get("matched") or [] if e["how"] in ("title", "contradicted")}
    for row in rows:
        marks = []
        if row["coverage"] == "single":
            words = title_words(row["name"])
            for target in targets:
                if not episode_in(row["name"], *target):
                    continue
                other = next((key for key, title in distinct.items() if key != target and _holds(words, title)), None)
                if other or (_tag(target) in disputed and not _holds(words, distinct.get(target) or [])):
                    marks.append(_tag(target))
        elif row.get("unlisted"):
            marks = [_tag(t) for t in targets if _tag(t) in disputed]
        else:
            marks = [e["target"] for e in row.get("matched") or [] if e["how"] == "number" and e["target"] in disputed]
        if marks:
            row["disputed"] = marks


async def scout(tb, job, targets, titles, extra_queries=(), searches=6, exclude=(), log=None, peek_log=None,
                names=None, letters=0):
    """Search, judge, peek and rank; returns (rows, searched, dropped, failing).

    failing: searches errored and none found a copy, so the index is failing;
    that is not proof no copy exists (an empty answer is: it is reported as
    nothing usable). Repeated searches come from the toolbox's cache and do
    not count against the allowance. peek_log: the cost ledger's list of
    packs looked inside (listed or not, wanted files found), appended to.
    names: TMDB's episode names for the wanted seasons; a pack's files are
    matched by those titles too (match()), and each listed row carries how its
    wanted episodes were found ("matched") and any numbering dispute."""
    plan = list(dict.fromkeys([*(searchable(q) for q in extra_queries), *queries(titles, targets, job.media_type, job.year)]))
    bare = {searchable(t).lower() for t in titles[:1]}
    found, searched, used, errors = {}, [], 0, 0
    for query in plan:
        if used >= searches:
            break
        cap = float(((job.preferences or {}).get("values") or {}).get("max_file_size_gb") or 0)
        usable = [c for c in found.values() if c["seeders"] >= 5 and (c["coverage"] == "pack" or not cap or c["size"] <= cap * 1e9)]
        try:
            # Queue for a slot while there is nothing usable to rank yet;
            # once there is, wait only briefly (other requests share slots).
            raw, cached = await tb.index_search(query, wait=SHORT_WAIT if usable else WAIT, log=log,
                                                scope=getattr(job, "id", ""))
        except ToolError:
            if searched:
                break  # searches are rationed: rank what was found
            raise
        except Exception:
            # Unreachable or a bad answer: the search still used its slot.
            used, errors = used + 1, errors + 1
            continue
        used += 0 if cached else 1
        searched.append(query)
        for row in raw or []:
            candidate = judge(row, job, targets, titles, exclude)
            if candidate and candidate["info_hash"] and candidate["info_hash"] not in found:
                found[candidate["info_hash"]] = candidate
        if query.lower() in bare and len(raw) >= CAPPED:
            # The index stops at 100 names: when other shows named after this
            # one crowd it ("Naruto Shippuden"), search again without them.
            words = blockers(raw, titles)
            if words:
                plan.insert(plan.index(query) + 1, " ".join([query, *(f"-{w}" for w in words)]))
        # Searches are rationed across every request: stop once there is a
        # real choice of healthy copies, one of them well seeded.
        # The well-seeded one must name a quality that fits: an unlabelled
        # pack can turn out 480p, so it is no reason to stop looking.
        healthy = [c for c in found.values() if c["seeders"] >= 5]
        promising = [c for c in healthy if c["seeders"] >= WELL_SEEDED and _rank(c["quality"]) >= _rank(job.min_quality)]
        if len(healthy) >= ENOUGH and promising:
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
            evidence = []
            try:
                chosen = await peek(candidate["apibay_id"], targets, job.media_type, titles, exclude,
                                    names=names, letters=letters, evidence=evidence)
            except (httpx.HTTPError, ValueError):
                chosen = None
            if peek_log is not None:
                peek_log.append({"id": candidate["apibay_id"], "listed": chosen is not None, "wanted": len(chosen or {}),
                                 **({"order": [e for e in evidence if e["how"] in ("title", "contradicted")]}
                                    if any(e["how"] in ("title", "contradicted") for e in evidence) else {})})
            if chosen is None and job.media_type != "movie" and spans(candidate, targets):
                # The indexer has no file list, but the name covers the
                # episodes: the download picks them once its list arrives.
                episode_size = candidate["size"] // max(candidate["files"], 1)
                if not cap or episode_size <= cap * 1e9:
                    rows.append({
                        **candidate,
                        "chosen": [f"{UNLISTED}S{s:02d}E{e:02d}" for s, e in targets],
                        "covers": [list(t) for t in targets],
                        "unlisted": True,
                        "episode_size": episode_size,
                    })
                continue
            if not chosen:
                continue  # no wanted episode in it, or its listing is unknown
            files = list(chosen.values())
            raw = candidate["raw"] or any(RAW.search(f["name"]) for f in files)  # "[Group-raws] Show [02].mkv"
            candidate = {
                **candidate,
                "raw": raw,
                "subs": candidate["subs"] and not raw,
                "chosen": [f["name"] for f in files],
                "covers": [list(t) for t in chosen if t],
                "episode_size": max(f["size"] for f in files),
                "matched": evidence,
            }
        else:
            candidate = {**candidate, "episode_size": candidate["size"]}
        if cap and candidate["episode_size"] > cap * 1e9:
            continue
        rows.append(candidate)
    if job.media_type != "movie":
        _disputes(rows, targets, names, titles, letters)
    rows.sort(key=lambda c: -score(c, job))  # packs now know what they cover
    for number, row in enumerate(rows, 1):
        row["rid"] = f"r{number}"
    return rows, searched, len(found) - len(rows), bool(errors) and not found


async def titles_for(tb, job, season, targets=()):
    """Names to search by (the title, then romanised and English
    alternatives), names to exclude (other seasons', and a same-named show's
    wanted episodes) and a note naming other titles that share this name."""
    titles, exclude = [job.title], []
    kind = "tv" if job.media_type == "tv" else "movie"
    try:
        data = await tb.tmdb_get(f"/{kind}/{job.tmdb_id}/alternative_titles")
    except Exception:
        data = {}
    own = compact(job.title)
    alternatives = sorted(data.get("results") or data.get("titles") or [], key=lambda a: a.get("iso_3166_1") != "JP")
    for alternative in alternatives:
        label, name = str(alternative.get("type") or "").lower(), alternative.get("title") or ""
        other = re.search(r"season\s*(\d+)", label)
        if other and int(other.group(1)) != season:
            exclude.append(name)
        elif (
            alternative.get("iso_3166_1") in ("JP", "US", "GB")
            and "abbreviation" not in label
            and latin(name)
            and not (compact(name).startswith(own) and compact(name) != own)  # a part or spin-off
        ):
            titles.append(name)
    unique = {}
    for title in titles:
        unique.setdefault(compact(title), title)
    labels, episodes = await _namesakes(tb, job, season, targets)
    titles = list(unique.values())[:3]
    # Another season named like this one ("Love is War?") would exclude it.
    forms = [compact(form) for title in titles for form in (title, main_part(title)) if form]
    exclude = [other for other in exclude if not any(form.startswith(compact(other)) for form in forms)]
    return titles, exclude + episodes, "; ".join(labels)


async def _namesakes(tb, job, season, targets):
    """Other titles with exactly this name, and their own names for the
    wanted episodes: releases of the live-action One Piece carry its
    episode titles, not the anime's."""
    kind = "tv" if job.media_type == "tv" else "movie"
    try:
        data = await tb.tmdb_get(f"/search/{kind}", query=job.title)
    except Exception:
        return [], []
    labels, episodes = [], []
    for result in (data.get("results") or [])[:20]:
        name = result.get("name") or result.get("title") or ""
        if result.get("id") == job.tmdb_id or compact(name) != compact(job.title):
            continue
        year = (result.get("first_air_date") or result.get("release_date") or "")[:4]
        labels.append(f"{name} ({year or 'year unknown'}, original language {result.get('original_language') or 'unknown'})")
        if kind == "tv" and targets:
            try:
                listing = await tb.tmdb_get(f"/tv/{result['id']}/season/{season}")
            except Exception:
                listing = {}
            wanted = {e for s, e in targets if s == season}
            for entry in listing.get("episodes") or []:
                title = entry.get("name") or ""
                # Only distinctive names: "Episode 2" says nothing.
                if entry.get("episode_number") in wanted and len(_plain(title).split()) >= 3 and not re.match(r"(?i)episode\s*\d", title):
                    episodes.append(title)
        if len(labels) >= 2:
            break
    return labels, episodes


def order_note(rows, peek_log=()):
    """One line on how the episode order was settled from TMDB's titles and
    the real file lists, or "" when no listing disagreed with TMDB."""
    found, wrong = {}, {}
    for row in rows:
        for e in row.get("matched") or []:
            if e.get("how") == "title":
                found.setdefault((e["target"], e["title"]), set()).add(e["numbered"] or "an unnumbered file")
    for peek in peek_log or ():
        for e in peek.get("order") or []:
            if e.get("how") == "contradicted":
                wrong.setdefault(e["target"], set()).add(f'"{e.get("is_title") or e.get("is_episode")}"')
    if not found and not wrong:
        return ""
    parts = [f'TMDB\'s {target} "{title}" is numbered {", ".join(sorted(numbers))} in the packs listed, found by its title '
             "in their file lists; those files were chosen" for (target, title), numbers in sorted(found.items())]
    parts += [f"a file numbered as {target} is another episode by its title ({', '.join(sorted(titles))}) and was not chosen"
              for target, titles in sorted(wrong.items())]
    return "Episode order: " + "; ".join(parts) + "."


def table(rows):
    """The short list in a few tokens a row."""
    lines = []
    for row in rows:
        covered = len(row.get("chosen") or [])
        what = "single release" if row["coverage"] == "single" else f"pack of {row['files']} files, {covered} wanted file{'s' if covered != 1 else ''} chosen"
        if row.get("unlisted"):
            what = f"pack of {row['files']} files, not listed by the indexer (the wanted episode is picked when it starts)"
        remapped = [e for e in row.get("matched") or [] if e.get("how") == "title"]
        hints = ", ".join(h for h, on in (("original audio named", row["dual"]), ("subtitles likely", row["subs"]),
                                          ("raw: no subtitles", row.get("raw")),
                                          ("; ".join(f"{e['target']} found by its TMDB title as file {e['numbered'] or '(unnumbered)'}"
                                                     for e in remapped), remapped),
                                          ("episode title matches the file", any(e.get("how") == "title_agrees" for e in row.get("matched") or [])),
                                          (f"NUMBERING DISPUTED for {', '.join(row.get('disputed') or [])}: its number may be "
                                           "another episode", row.get("disputed")),
                                          ("ALREADY ABANDONED for this request (stalled or wrong)", row.get("abandoned"))) if on)
        size = f"{row['episode_size'] / 1e9:.2f} GB" if row["coverage"] == "single" else (
            f"{row['episode_size'] / 1e9:.2f} GB {'an episode, estimated' if row.get('unlisted') else 'chosen'} "
            f"of a {row['size'] / 1e9:.1f} GB pack")
        lines.append(
            f"{row['rid']} · {size} · {row['seeders']} seeds · {row['quality']} {row['source']} · "
            f"{what}{' · ' + hints if hints else ''} · {row['name'][:90]}"
        )
    return "\n".join(lines)
