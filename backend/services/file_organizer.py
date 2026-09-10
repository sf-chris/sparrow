"""
Smart file organiser — parses filenames with the release parser and places
each video precisely, updating the per-episode inventory as it goes.

The parse is tiered: deterministic regex handles conventional names for free;
anything ambiguous is batch-parsed by Haiku (cached forever), so anime
numbering, fansub formats, and multi-episode files all land correctly.
"""
from __future__ import annotations
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Optional

from .release_parser import parse_releases, parse_release_name, ParsedRelease

DATA_DIR = "./data"

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".ts", ".m2ts"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".vtt", ".sup"}
SAMPLE_PATTERNS = re.compile(r'\bsample\b|\bpromo\b|\btrailer\b', re.IGNORECASE)


# ─── Path helpers ─────────────────────────────────────────────────────────────

def _safe_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s)
    return s.strip(". ")[:200]


def _find_video_files(path: Path) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() in VIDEO_EXTENSIONS and not SAMPLE_PATTERNS.search(path.name):
            return [path]
        return []
    files = []
    for p in sorted(path.rglob("*")):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and not SAMPLE_PATTERNS.search(p.name):
            files.append(p)
    # Sort by name so episodes come out in order
    return sorted(files, key=lambda f: f.name)


def _movie_dest(library: Path, title: str, year: Optional[int], quality: str, ext: str) -> Path:
    year_tag = f" ({year})" if year else ""
    q_tag = f" [{quality}]" if quality and quality != "unknown" else ""
    folder = _safe_name(f"{title}{year_tag}")
    fname = _safe_name(f"{title}{year_tag}{q_tag}{ext}")
    return library / "Movies" / folder / fname


def _tv_dest(library: Path, series: str, season: int, episode: int, quality: str, ext: str, ep_title: str = "") -> Path:
    ep_tag = f"S{season:02d}E{episode:02d}"
    title_tag = f" - {_safe_name(ep_title)}" if ep_title else ""
    q_tag = f" [{quality}]" if quality and quality != "unknown" else ""
    fname = _safe_name(f"{series} - {ep_tag}{title_tag}{q_tag}{ext}")
    return library / "TV Shows" / _safe_name(series) / f"Season {season:02d}" / fname


def _move_with_subs(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    for sub_ext in SUBTITLE_EXTENSIONS:
        sub_src = src.with_suffix(sub_ext)
        if sub_src.exists():
            shutil.move(str(sub_src), str(dst.with_suffix(sub_ext)))


# ─── Main organise function ───────────────────────────────────────────────────

def _infer_season_context(staging: Path, parsed_root: ParsedRelease) -> Optional[int]:
    """Season implied by the torrent/staging folder name, if unambiguous."""
    if len(parsed_root.seasons) == 1:
        return parsed_root.seasons[0]
    return None


async def organize_download(
    staging_path: str,
    library_dir: str,
    media_type,  # MediaType enum
    metadata: dict,
    data_dir: str = DATA_DIR,
    anthropic_api_key: str = "",
    dry_run: bool = False,
    storage=None,
) -> list[dict]:
    """
    Move completed download from staging to library.

    Each video file is parsed individually (regex tier, Haiku for the weird
    ones — cached). Files inside a season-pack folder inherit the folder's
    season when their own name doesn't say.

    Returns list of {src, dst, success, error, episodes, quality} operations —
    `episodes` is the [[season, episode], ...] pairs the placed file contains,
    so callers can update the per-episode inventory.
    """
    staging = Path(staging_path)
    library = Path(library_dir)
    title = metadata.get("title", staging.name)
    year = metadata.get("year")
    quality = metadata.get("quality", "unknown")

    video_files = _find_video_files(staging)
    if not video_files:
        return [{"src": str(staging), "dst": "", "success": False, "error": "No video files found in staging path"}]

    # Parse the torrent/folder name once (season context for pack contents),
    # then every file name in one cached/batched pass.
    parsed_root = parse_release_name(staging.name)
    parsed_files = await parse_releases(
        [v.name for v in video_files], storage, anthropic_api_key,
    )
    season_ctx = _infer_season_context(staging, parsed_root)

    from ..models import MediaType as MT
    is_movie = (media_type == MT.MOVIE)
    is_unknown = (media_type == MT.UNKNOWN)

    if is_unknown:
        if any(p.episodes or p.is_season_pack for p in parsed_files) or parsed_root.media_type == "tv":
            is_movie = False
            is_unknown = False
        else:
            is_movie = len(video_files) == 1

    operations = []

    for video, parsed in zip(video_files, parsed_files):
        ext = video.suffix.lower()
        op: dict = {"src": str(video), "dst": "", "success": False, "error": "",
                    "episodes": [], "quality": parsed.quality if parsed.quality != "unknown" else quality}

        if is_movie or (is_unknown and len(video_files) == 1):
            dst = _movie_dest(library, title, year, op["quality"], ext)

        else:
            episodes = [list(e) for e in parsed.episodes]
            # Absolute/unknown-season numbering inherits the folder's season
            fixed = []
            for s, e in episodes:
                if s == 0:
                    s = season_ctx if season_ctx is not None else 1
                fixed.append([s, e])
            episodes = fixed

            if not episodes and season_ctx is not None:
                # File inside a season folder with an unparseable name: try to
                # pull a bare episode number out of it.
                m = re.search(r"\b(?:e|ep|episode)?\s*0*(\d{1,3})\b", video.stem, re.I)
                if m:
                    episodes = [[season_ctx, int(m.group(1))]]

            if not episodes:
                # Give it a best-effort home rather than failing
                dst = library / "TV Shows" / _safe_name(title) / _safe_name(video.name)
                op["error"] = f"Could not parse episode info from '{video.name}' — placed in series root"
            else:
                season, episode = episodes[0]
                if len(episodes) > 1:
                    # Multi-episode file: name with the full range
                    last = episodes[-1][1]
                    ep_tag = f"S{season:02d}E{episode:02d}-E{last:02d}"
                    q_tag = f" [{op['quality']}]" if op["quality"] and op["quality"] != "unknown" else ""
                    fname = _safe_name(f"{title} - {ep_tag}{q_tag}{ext}")
                    dst = library / "TV Shows" / _safe_name(title) / f"Season {season:02d}" / fname
                else:
                    ep_title = metadata.get("episodes", {}).get(f"S{season:02d}E{episode:02d}", "")
                    dst = _tv_dest(library, title, season, episode, op["quality"], ext, ep_title)
                op["episodes"] = episodes

        op["dst"] = str(dst)

        if not dry_run:
            try:
                _move_with_subs(video, dst)
                op["success"] = True
            except Exception as e:
                op["error"] = op.get("error", "") + f" Move failed: {e}"
        else:
            op["success"] = True

        operations.append(op)

    # Clean up empty staging dir
    if not dry_run and staging.is_dir():
        try:
            remaining_videos = _find_video_files(staging)
            if not remaining_videos:
                shutil.rmtree(str(staging), ignore_errors=True)
        except Exception:
            pass

    return operations


def scan_library(library_dir: str) -> list[dict]:
    """Walk library and return items found (for import into Sparrow)."""
    library = Path(library_dir)
    if not library.exists():
        return []

    items = []

    movies_dir = library / "Movies"
    if movies_dir.exists():
        for folder in sorted(movies_dir.iterdir()):
            if not folder.is_dir():
                continue
            videos = _find_video_files(folder)
            if not videos:
                continue
            m = re.match(r'^(.+?)\s*\((\d{4})\)$', folder.name)
            items.append({
                "path": str(folder),
                "title": m.group(1).strip() if m else folder.name,
                "year": int(m.group(2)) if m else None,
                "media_type": "movie",
                "files": [str(v) for v in videos],
            })

    tv_dir = library / "TV Shows"
    if tv_dir.exists():
        for series_folder in sorted(tv_dir.iterdir()):
            if not series_folder.is_dir():
                continue
            videos = _find_video_files(series_folder)
            if not videos:
                continue
            items.append({
                "path": str(series_folder),
                "title": series_folder.name,
                "year": None,
                "media_type": "tv",
                "files": [str(v) for v in videos],
                "episodes": scan_show_episodes(series_folder),
            })

    return items


def scan_show_episodes(series_folder: Path) -> dict:
    """Build the per-episode inventory for a show folder from what's on disk.

    Returns {"1": {"3": {"quality": ..., "path": ..., "size_bytes": ...}}}.
    Uses the deterministic parser only (fast, no network) — files organized by
    Sparrow always carry SxxExx markers, and Season folders give context for
    the rest.
    """
    inventory: dict = {}
    for video in _find_video_files(series_folder):
        parsed = parse_release_name(video.name)
        episodes = [list(e) for e in parsed.episodes]
        if not episodes:
            sm = re.search(r"season\s*0*(\d{1,2})", str(video.parent.name), re.I)
            em = re.search(r"\b(?:e|ep|episode)?\s*0*(\d{1,3})\b", video.stem, re.I)
            if sm and em:
                episodes = [[int(sm.group(1)), int(em.group(1))]]
        for s, e in episodes:
            if s == 0:
                sm = re.search(r"season\s*0*(\d{1,2})", str(video.parent.name), re.I)
                s = int(sm.group(1)) if sm else 1
            try:
                size = video.stat().st_size
            except OSError:
                size = 0
            inventory.setdefault(str(s), {})[str(e)] = {
                "quality": parsed.quality,
                "path": str(video),
                "size_bytes": size,
                "group": parsed.group,
                "verified": False,
            }
    return inventory


def verify_organized_operations(operations: list[dict], media_type, metadata: dict) -> dict:
    """Cheap post-organize check for silent organization confidence."""
    title = (metadata or {}).get("title", "")
    issues: list[str] = []
    checked = 0
    from ..models import MediaType as MT

    for op in operations:
        if not op.get("success"):
            issues.append(op.get("error") or f"Move failed for {op.get('src', 'unknown file')}")
            continue
        checked += 1
        dst = Path(op.get("dst", ""))
        dst_text = str(dst)
        if not dst.exists():
            issues.append(f"Destination missing after organize: {dst_text}")
        if media_type == MT.TV:
            if "TV Shows" not in dst.parts:
                issues.append(f"TV item placed outside TV Shows: {dst_text}")
            if not re.search(r'[Ss]\d{1,2}[Ee]\d{1,4}', dst.name) and "Season" not in dst_text:
                issues.append(f"TV filename has no episode marker: {dst.name}")
        elif media_type == MT.MOVIE:
            if "Movies" not in dst.parts:
                issues.append(f"Movie item placed outside Movies: {dst_text}")
        if title:
            title_words = {w.lower() for w in re.findall(r'\w+', title) if len(w) > 2}
            path_words = {w.lower() for w in re.findall(r'\w+', dst_text)}
            if title_words and len(title_words & path_words) == 0:
                issues.append(f"Destination does not resemble metadata title '{title}': {dst_text}")

    return {
        "ok": not issues,
        "checked": checked,
        "issues": issues,
    }
