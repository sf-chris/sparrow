"""Isolated subtitle normalisation and measured timing correction.

The worker parses a candidate track into plain cues, preserves the original and
writes a playable WebVTT copy. A timing correction is applied only when the
coordinator supplies one measured against caption-independent speech evidence
(subtitle_sync.py); nothing here decides that a track is correct.
"""

from __future__ import annotations
import hashlib
import html
import json
import re
import sys
from pathlib import Path

DRAWING = re.compile(r"\{[^}]*\\p[1-9]")


def cues_from_text(text, format_name):
    import pysubs2

    if format_name not in ("srt", "vtt", "ass", "ssa"):
        raise ValueError("Use an SRT, VTT or ASS text subtitle.")
    if len(text.encode("utf8")) > 2 * 1024 * 1024:
        raise ValueError("Subtitle files must be smaller than 2 MB.")
    parsed = pysubs2.SSAFile.from_string(text, format_=format_name)
    cues = []
    for entry in parsed:
        if DRAWING.search(entry.text):
            continue  # ASS vector shapes are typesetting, not readable text.
        clean = entry.plaintext.strip()
        if format_name == "vtt":
            clean = html.unescape(clean)
        if entry.is_comment or not clean:
            continue
        if entry.start < 0 or entry.end <= entry.start:
            raise ValueError("The subtitle contains invalid cue timings.")
        if len(clean) > 2000:
            raise ValueError("A subtitle cue is too large to display safely.")
        cues.append(
            {"start": entry.start / 1000, "end": entry.end / 1000, "text": clean}
        )
    if not cues or len(cues) > 20000:
        raise ValueError("Choose a subtitle with 1–20,000 readable cues.")
    # ASS files commonly group events by style (signs after dialogue); play order
    # is by time, so sort rather than reject. The sort is stable for ties.
    return sorted(cues, key=lambda cue: cue["start"])


def stamp(seconds, separator="."):
    milliseconds = round(max(0, seconds) * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def render(cues, vtt=False):
    lines = ["WEBVTT", ""] if vtt else []
    for index, cue in enumerate(cues, 1):
        lines.extend(
            [
                str(index),
                f'{stamp(cue["start"],"." if vtt else ",")} --> {stamp(cue["end"],"." if vtt else ",")}',
                html.escape(cue["text"], quote=False) if vtt else cue["text"],
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def process(packet):
    from .media_state import file_version
    from .subtitle_sync import apply

    folder = Path(packet["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    video = Path(packet["video"])
    if file_version(video) != packet["version"]:
        raise ValueError("The video changed before subtitle preparation.")
    original = packet["text"]
    cues = cues_from_text(original, packet["format"])
    if max(c["end"] for c in cues) > packet["duration"] + 120:
        raise ValueError(
            "These subtitles appear to belong to a different-length video."
        )
    correction = packet.get("correction")
    prepared = apply(cues, correction)
    (folder / "original.txt").write_text(original, encoding="utf8")
    if file_version(video) != packet["version"]:
        raise ValueError("The video changed while its subtitles were being prepared.")
    (folder / "prepared.vtt").write_text(render(prepared, vtt=True), encoding="utf8")
    (folder / "original.vtt").write_text(render(cues, vtt=True), encoding="utf8")
    return {
        "unchanged": not correction,
        "correction": correction,
        "cue_count": len(cues),
        "original_sha256": hashlib.sha256(original.encode("utf8")).hexdigest(),
    }


def main(path):
    request = Path(path)
    try:
        packet = json.loads(request.read_text(encoding="utf8"))
        result = {"ok": True, "value": process(packet)}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:2000]}
    request.with_name("result.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf8"
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
