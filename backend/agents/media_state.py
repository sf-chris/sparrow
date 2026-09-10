"""Shared, evidence-based media availability for tools, projections and playback."""

from __future__ import annotations

from pathlib import Path


def file_version(path: str | Path) -> dict:
    stat = Path(path).stat()
    return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def media_state(record: dict, path: str = "") -> str:
    """Retain ownership when unavailable; never turn filename guesses into readiness.

    Remote observations are supplied by the node ledger, never by stat-ing a
    Windows path on Linux. A playback request must still check live availability.
    """
    if record.get("node_id"):
        if not record.get("available"):
            return "unavailable"
        return "ready" if record.get("verified") else "verifying"
    location = record.get("path") or path
    try:
        if not location or not Path(location).is_file():
            return "unavailable"
        version = file_version(location)
    except OSError:
        return "unavailable"
    if not record.get("verified"):
        return "verifying"
    if record.get("file_version") and record["file_version"] != version:
        return "verifying"
    return "ready"


LANGUAGES = {
    "english": "en",
    "eng": "en",
    "spanish": "es",
    "spa": "es",
    "french": "fr",
    "fra": "fr",
    "fre": "fr",
    "german": "de",
    "deu": "de",
    "ger": "de",
    "japanese": "ja",
    "jpn": "ja",
    "italian": "it",
    "ita": "it",
    "portuguese": "pt",
    "por": "pt",
    "chinese": "zh",
    "zho": "zh",
    "chi": "zh",
    "korean": "ko",
    "kor": "ko",
}


def language_code(value: str) -> str:
    value = (value or "").strip().lower().replace("_", "-").split("-")[0]
    return LANGUAGES.get(value, value)


def audio_satisfies(record: dict, preference: str, original_language: str = "") -> bool:
    if not preference or preference == "any":
        return True
    wanted = language_code(
        original_language if preference == "original" else preference
    )
    tracks = record.get("audio_languages") or [
        t.get("language", "") for t in record.get("audio_tracks", [])
    ]
    return bool(wanted and wanted in {language_code(t) for t in tracks})
