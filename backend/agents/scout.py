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
from .release_match import covers_season, episode_in, other_season, sequel_of
from .runtime import ToolError

QUALITY_RANK = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4}
ROWS = 8
ENOUGH = 4  # healthy candidates that end the search early
PEEKS = 4
WAIT = 900  # seconds to queue for a search slot while nothing is found yet
DUB_ONLY = re.compile(r"(?i)\b(eng(lish)?[\s._-]?dub(bed)?|dubbed|dub([\s._-]?only)?)\b")
DUAL = re.compile(r"(?i)\b(dual[\s._-]?audio|multi[\s._-]?audio|jap(anese)?|jpn|original[\s._-]?audio)\b")
SUBS = re.compile(r"(?i)\b(multi[\s._-]?subs?|e?subs?|eng[\s._-]?subs?|subbed|softsubs?|cr|nf|amzn|dsnp|hidive|web[\s._-]?dl)\b")
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
        "subs": bool(SUBS.search(name)),
        "complete": bool(parsed.is_complete_series or parsed.is_season_pack),
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
    )


EXTRAS = re.compile(r"(?i)(?:^|[\s/_.\-\[(])(?:specials?|extras?|bonus|ova|oad|movies?|ncop|nced|creditless|trailers?|pv)(?=$|[\s/_.\-\])])")


def _other_part(path, titles, season, exclude):
    """A pack file that belongs to another season or show: a folder or file
    named for it ("Tokyo Ghoul Root A/...", "Season 2/...")."""
    if other_season(path, season) and not covers_season(path, season):
        return True
    if excluded(path, exclude) or EXTRAS.search(path):
        return True
    forms = {compact(form) for title in titles for form in (title, main_part(title)) if form}
    for segment in str(path).split("/"):
        opening = compact(NOISE.sub("", segment))
        if any(form and opening.startswith(form) for form in forms) and not titled(segment, titles):
            return True
    return False


async def peek(apibay_id, targets, media_type, titles=(), exclude=()):
    """The wanted episodes' files inside a pack ({(season, episode): file}):
    empty when none are there, None when the indexer cannot list it."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"https://apibay.org/f.php?id={urllib.parse.quote(apibay_id)}")
        listing = response.json()
    from .tools import _apibay_indexed_value as value

    entries = []
    for entry in listing if isinstance(listing, list) else []:
        if isinstance(entry, dict):
            name, size = value(entry.get("name"), ""), value(entry.get("size"), 0)
            if name and name != "Filelist not found":
                entries.append((str(name), int(size or 0)))
    return choose(entries, targets, media_type, titles, exclude) if entries else None


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
    stems = {_plain(n.rsplit("/", 1)[-1].rsplit(".", 1)[0]) for n in names if not n.startswith(UNLISTED)}
    for name, _ in entries:
        base = name.rsplit("/", 1)[-1]
        if not base.lower().endswith(SUBTITLE_FILES) or name in names:
            continue
        stem = _plain(base.rsplit(".", 1)[0])
        if any(s and stem.startswith(s) for s in stems) or (
            any(episode_in(base, *t) for t in targets) and not _other_part(name, titles, season, ())
        ):
            names.append(name)
    return names


def choose(entries, targets, media_type, titles=(), exclude=()):
    """The wanted episodes' files among (name, size) entries."""
    season = targets[0][0] if targets else 1
    files = []
    for name, size in entries:
        if not name.lower().endswith(VIDEO):
            continue
        if "sample" in name.lower() or (media_type != "movie" and _other_part(name, titles, season, exclude)):
            continue
        for target in ([None] if media_type == "movie" else targets):
            if target is None or episode_in(name.rsplit("/", 1)[-1], *target):
                files.append((target, {"name": name, "size": size}))
    chosen = {}
    for target, file in files:
        current = chosen.get(target)
        # The standard cut over alternates, then the largest: not a sample.
        key = (not ALTERNATE.search(file["name"]), file["size"])
        if current is None or key > (not ALTERNATE.search(current["name"]), current["size"]):
            chosen[target] = file
    return chosen


async def scout(tb, job, targets, titles, extra_queries=(), searches=6, exclude=()):
    """Search, judge, peek and rank; returns (rows, searched, dropped, failing).

    failing: the index answered nothing even for the bare title, which means
    it is failing, not that no copy exists. Repeated searches come from the
    toolbox's cache and do not count against the allowance."""
    plan = list(dict.fromkeys([*(searchable(q) for q in extra_queries), *queries(titles, targets, job.media_type, job.year)]))
    bare = {searchable(t).lower() for t in titles[:1]}
    found, searched, used, failing = {}, [], 0, False
    for query in plan:
        if used >= searches:
            break
        try:
            # Queue for a slot while there is nothing to rank yet.
            raw, cached = await tb.index_search(query, wait=0 if found else WAIT)
        except ToolError:
            if searched:
                break  # searches are rationed: rank what was found
            raise
        except Exception:
            continue
        used += 0 if cached else 1
        searched.append(query)
        failing = failing or (query.lower() in bare and not raw)
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
                chosen = await peek(candidate["apibay_id"], targets, job.media_type, titles, exclude)
            except (httpx.HTTPError, ValueError):
                chosen = None
            if chosen is None and job.media_type != "movie" and spans(candidate, targets):
                # The indexer has no file list, but the name covers the
                # episodes: the download picks them once its list arrives.
                rows.append({
                    **candidate,
                    "chosen": [f"{UNLISTED}S{s:02d}E{e:02d}" for s, e in targets],
                    "covers": [list(t) for t in targets],
                    "unlisted": True,
                    "episode_size": candidate["size"] // max(candidate["files"], 1),
                })
                continue
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
    return rows, searched, len(found) - len(rows), failing and not found


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


def table(rows):
    """The short list in a few tokens a row."""
    lines = []
    for row in rows:
        covered = len(row.get("chosen") or [])
        what = "single release" if row["coverage"] == "single" else f"pack of {row['files']} files, {covered} wanted file{'s' if covered != 1 else ''} chosen"
        if row.get("unlisted"):
            what = f"pack of {row['files']} files, not listed by the indexer (the wanted episode is picked when it starts)"
        hints = ", ".join(h for h, on in (("original audio named", row["dual"]), ("subtitles likely", row["subs"])) if on)
        lines.append(
            f"{row['rid']} · {row['episode_size'] / 1e9:.2f} GB · {row['seeders']} seeds · {row['quality']} {row['source']} · "
            f"{what}{' · ' + hints if hints else ''} · {row['name'][:90]}"
        )
    return "\n".join(lines)
