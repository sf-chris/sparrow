"""Whether a measured copy runs as long as the catalogue says.

A running time is evidence about identity, not a minute count to match. Film
is 24 frames a second (23.976 on disc); European TV, DVDs and some Blu-rays
play it at 25, so the same cut runs about 4% shorter, and a catalogue figure
can come from either. TMDB lists Four Lions at 97 minutes, its 25 fps length;
the film-speed copy runs 101. Catalogue figures also round and disagree about
credits, and a release can be another cut it names (Extended, Director's Cut).
Samples and double-length files never pass.
"""

from __future__ import annotations

import re

FILM_FPS, PAL_FPS = 24000 / 1001, 25.0

# Words a release uses for another cut of the same film.
CUT = re.compile(
    r"extended|director'?s[ ._-]?cut|unrated|uncut|final[ ._-]cut|special[ ._-]edition|"
    r"ultimate[ ._-](?:cut|edition)|redux|theatrical|imax|"
    r"(?:international|alternate|alternative|rogue|producer'?s|tv)[ ._-]cut",
    re.IGNORECASE,
)


def frame_rate(stream) -> float:
    """An ffprobe video stream's frame rate, or 0 when it gives none."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        top, _, bottom = str((stream or {}).get(key) or "").partition("/")
        try:
            rate = float(top) / float(bottom or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= rate <= 240:
            return round(rate, 3)
    return 0.0


def pal(fps) -> bool:
    rate = float(fps or 0)
    return 24.5 <= rate <= 25.5 or 49 <= rate <= 51


def film(fps) -> bool:
    return 23.5 <= float(fps or 0) < 24.5


def speeds(duration, fps=None) -> list:
    """The copy's length as measured, and at the other speed its frame rate
    allows: film speed for a 25 fps copy, 25 fps speed for a film-rate one."""
    seconds = float(duration)
    if pal(fps):
        return [seconds, seconds * PAL_FPS / FILM_FPS]
    if film(fps):
        return [seconds, seconds * float(fps) / PAL_FPS]
    return [seconds]


def at_film_speed(duration, fps=None) -> float:
    """A copy's length at film speed, for comparing two copies of one cut."""
    return float(duration) * PAL_FPS / FILM_FPS if pal(fps) else float(duration)


def names_cut(*names) -> bool:
    return any(CUT.search(str(name or "")) for name in names)


def runtime_matches(duration, minutes, approximate=False, fps=None, cut=False) -> bool:
    """Whether a measured duration is the catalogue's runtime.

    At either speed the frame rate allows: shorter by up to two minutes or 10%,
    longer by up to two minutes or, for short items, a quarter (credits can be a
    large part of a short film: Big Buck Bunny runs 9:56 against TMDB's 8
    minutes), and at least 5% either way for features. Never less than a minute
    either way, since catalogue runtimes are whole minutes. A quarter either way
    when the figure is a season-wide placeholder; a fifth shorter or two fifths
    longer when the release names another cut. Samples and double-length files
    never pass."""
    seconds = float(minutes or 0) * 60
    if not seconds:
        return False
    shorter = max(min(120, max(60, seconds * 0.1)), seconds * 0.05)
    longer = max(min(120, max(60, seconds * 0.25)), seconds * 0.05)
    if approximate:
        shorter, longer = max(shorter, seconds * 0.25), max(longer, seconds * 0.25)
    if cut:
        shorter, longer = max(shorter, seconds * 0.2), max(longer, seconds * 0.4)
    return any(-shorter <= length - seconds <= longer for length in speeds(duration, fps))


def mismatch(duration, minutes, approximate=False, fps=None) -> str:
    """Why a measured copy was refused, for the agent that measured it."""
    lengths = speeds(duration, fps)
    measured = f"{lengths[0] / 60:.1f} min"
    if len(lengths) > 1:
        other = "film speed" if pal(fps) else "25 fps speed"
        measured += f"; {lengths[1] / 60:.1f} min at {other}, since it plays at {float(fps):g} fps"
    placeholder = ", a season-wide figure" if approximate else ""
    return (
        f"Measured duration ({measured}) does not match the catalogue runtime "
        f"({float(minutes or 0):.0f} min{placeholder}). Already allowed: a few percent either way, "
        "either playback speed, and another cut the release names (Extended, Director's Cut). "
        "This copy is probably another title, a sample or an incomplete file: resolve its identity "
        "before recording it. Do not search for a cut to match a catalogue minute count."
    )


def episode_runtime(season_data, episode) -> tuple:
    """An episode's catalogue runtime and whether it is only a placeholder:
    one runtime for a whole season is not a measurement (TMDB gives every
    Psycho-Pass episode 28 minutes; they run 23). 0 when TMDB has none, and
    the caller falls back to the show's default, also a placeholder."""
    episodes = (season_data or {}).get("episodes", [])
    entry = next((e for e in episodes if int(e.get("episode_number") or 0) == int(episode)), {})
    runtimes = [e.get("runtime") for e in episodes if e.get("runtime")]
    expected = float(entry.get("runtime") or 0)
    return expected, not expected or (len(runtimes) >= 3 and len(set(runtimes)) == 1)
