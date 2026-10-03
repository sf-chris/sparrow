"""Configuration boundaries: path normalization and secret-safe API views."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import Quality, SparrowConfig, TorrentClientConfig


def normalize_media_path(value: str) -> str:
    """Return a stable absolute path without requiring it to exist yet."""
    value = (value or "").strip()
    if not value:
        return ""
    return str(Path(os.path.expandvars(value)).expanduser().resolve(strict=False))


def normalize_config(config: SparrowConfig) -> SparrowConfig:
    config.staging_dir = normalize_media_path(config.staging_dir)
    config.library_dir = normalize_media_path(config.library_dir)
    config.torrent_client.host = (config.torrent_client.host or "localhost").strip()
    return config


def validate_media_roots(config: SparrowConfig) -> None:
    roots = [Path(value) for value in (config.staging_dir, config.library_dir) if value]
    if any(path == Path(path.anchor) for path in roots):
        raise ValueError("Staging and library cannot be a filesystem root.")
    if len(roots) == 2:
        staging, library = roots
        if (
            staging == library
            or staging in library.parents
            or library in staging.parents
        ):
            raise ValueError(
                "Staging and library must be separate sibling roots; neither may contain the other."
            )


def in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def prepare_media_folder(value: str, *, writable: bool) -> None:
    """Make a chosen folder usable now, or say why it can't be.

    Only the last folder is created, so a mistyped parent never leaves a trail
    of new folders behind.
    """
    path = Path(value)
    where = " In Docker, choose a folder under /media." if in_container() else ""
    if not path.is_dir():
        if path.exists():
            raise ValueError(f"{value} is a file, not a folder.")
        # The suggested home for new folders may not exist yet either.
        suggested = path.parent == suggested_base() and path.parent.parent.is_dir()
        if not path.parent.is_dir() and not suggested:
            raise ValueError(f"{path.parent} isn’t on this server.{where}")
        try:
            path.mkdir(parents=suggested)
        except OSError:
            raise ValueError(f"Sparrow can’t create {value}.{where}") from None
    if not os.access(path, os.R_OK | os.X_OK):
        raise ValueError(f"Sparrow can’t open {value}.")
    if writable and not os.access(path, os.W_OK):
        raise ValueError(f"Sparrow can’t save into {value}.")


def suggested_base() -> Path:
    return Path("/media") if in_container() else Path.home() / "Sparrow"


def suggested_media_folders() -> dict[str, Any]:
    """Where a new library and incoming folder most likely belong."""
    base = suggested_base()
    children = (
        {child.name.lower(): child for child in base.iterdir() if child.is_dir()}
        if base.is_dir()
        else {}
    )

    def pick(names: tuple[str, ...], default: str) -> str:
        return str(next((children[n] for n in names if n in children), base / default))

    return {
        "library": pick(("library", "media", "movies", "films"), "Library"),
        "incoming": pick(("incoming", "temp", "downloads", "staging"), "Incoming"),
        "in_container": in_container(),
    }


def effective_tmdb_key(config: SparrowConfig) -> str:
    return config.tmdb_api_key or os.getenv("TMDB_API_KEY", "")


def effective_anthropic_key(config: SparrowConfig) -> str:
    return config.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")


_saved_config: Callable[[], SparrowConfig] | None = None


def track_saved_config(source: Callable[[], SparrowConfig]) -> None:
    """Let helpers without a config at hand read keys saved in Settings."""
    global _saved_config
    _saved_config = source


def effective_openai_key(config: SparrowConfig | None = None) -> str:
    """The OpenAI key saved in Settings, else OPENAI_API_KEY. Optional."""
    if config is None and _saved_config is not None:
        try:
            config = _saved_config()
        except Exception:
            config = None
    return (config.openai_api_key if config else "") or os.getenv("OPENAI_API_KEY", "")


def public_config(config: SparrowConfig) -> dict[str, Any]:
    """Serialize config for the browser without returning stored credentials."""
    data = config.to_dict()
    data["tmdb_api_key"] = ""
    data["anthropic_api_key"] = ""
    data["openai_api_key"] = ""
    data["tmdb_api_key_configured"] = bool(effective_tmdb_key(config))
    data["anthropic_api_key_configured"] = bool(effective_anthropic_key(config))
    data["openai_api_key_configured"] = bool(effective_openai_key(config))

    torrent = data["torrent_client"]
    torrent["password"] = ""
    torrent["password_configured"] = bool(config.torrent_client.password)
    # A full override URL may contain embedded credentials. Keep it tool-side.
    torrent["url"] = ""
    torrent["url_configured"] = bool(config.torrent_client.url)
    return data


def apply_config_update(
    config: SparrowConfig, values: Mapping[str, Any]
) -> SparrowConfig:
    """Apply an API patch while treating blank secret fields as "keep existing"."""
    if values.get("staging_dir") is not None:
        config.staging_dir = normalize_media_path(str(values["staging_dir"]))
    if values.get("library_dir") is not None:
        config.library_dir = normalize_media_path(str(values["library_dir"]))

    torrent_update = values.get("torrent_client")
    if torrent_update is not None:
        merged = config.torrent_client.to_dict()
        incoming = dict(torrent_update)
        for secret in ("password", "url"):
            if not incoming.get(secret):
                incoming.pop(secret, None)
        # Browser-only metadata must never reach the dataclass constructor.
        incoming.pop("password_configured", None)
        incoming.pop("url_configured", None)
        merged.update(incoming)
        config.torrent_client = TorrentClientConfig.from_dict(merged)

    if values.get("quality_preference") is not None:
        config.quality_preference = Quality(values["quality_preference"])
    if values.get("tmdb_api_key"):
        config.tmdb_api_key = str(values["tmdb_api_key"]).strip()
    if values.get("anthropic_api_key"):
        config.anthropic_api_key = str(values["anthropic_api_key"]).strip()
    if values.get("clear_tmdb_api_key"):
        config.tmdb_api_key = ""
    if values.get("openai_api_key"):
        config.openai_api_key = str(values["openai_api_key"]).strip()
    if values.get("clear_anthropic_api_key"):
        config.anthropic_api_key = ""
    if values.get("clear_openai_api_key"):
        config.openai_api_key = ""

    scalar_fields = (
        "onboarding_complete",
        "auto_organize",
        "seeding_ratio_limit",
        "seeding_time_hours",
        "prefer_smaller_files",
        "prefer_season_packs",
        "season_pack_size_limit_gb",
        "preferred_search_engines",
    )
    if values.get("preferred_search_engines") is not None and values[
        "preferred_search_engines"
    ] != ["apibay"]:
        raise ValueError(
            "This release supports the built-in title source only (apibay). Other source connectors are not installed."
        )
    for field in scalar_fields:
        if values.get(field) is not None:
            setattr(config, field, values[field])
    if values.get("max_active_transfers") is not None:
        config.max_active_transfers = max(1, int(values["max_active_transfers"]))
    for field in ("smart_model", "cheap_model"):
        if values.get(field) is not None:
            setattr(config, field, str(values[field]).strip())
    normalize_config(config)
    validate_media_roots(config)
    return config
