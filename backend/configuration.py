"""Configuration boundaries: path normalization and secret-safe API views."""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
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


def _unescape_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def bind_mounts(mountinfo: str | None = None) -> list[tuple[PurePosixPath, Path]]:
    """(folder on its own disk, where this process sees it) for each bind mount."""
    if mountinfo is None:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text()
        except OSError:
            return []
    mounts = []
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        root, point = _unescape_mount(fields[3]), _unescape_mount(fields[4])
        if root != "/" and point != "/":
            mounts.append((PurePosixPath(root), Path(point)))
    return mounts


def mounted_path(value: str, mounts=None) -> Path | None:
    """Where a folder named by its path on the host appears here, if it's mounted.

    Docker can only show folders it mounts, so /home/me/Media/Library typed in the
    browser is /media/Library inside the container. A mount's root is relative to
    its own disk, so /home/me/Media can appear as /me/Media when /home is a disk.
    """
    parts = PurePosixPath(value).parts
    best: tuple[int, Path] | None = None
    for root, point in bind_mounts() if mounts is None else mounts:
        inner = root.parts[1:]
        if not inner:
            continue
        for start in range(1, len(parts) - len(inner) + 1):
            # One-folder roots must match from the top; deeper ones are specific enough.
            if start > 1 and len(inner) < 2:
                break
            if parts[start : start + len(inner)] == inner:
                if best is None or len(inner) > best[0]:
                    best = (len(inner), point.joinpath(*parts[start + len(inner) :]))
                break
    return best[1] if best else None


def usable_media_folder(value: str, *, writable: bool) -> str:
    """The folder Sparrow will use for a chosen path, created if it's missing.

    A host path that Docker mounts elsewhere is used at its mounted location.
    """
    path = Path(value)
    where = " In Docker, choose a folder under /media." if in_container() else ""
    if not path.is_dir():
        mounted = mounted_path(value)
        if mounted and (mounted.is_dir() or mounted.parent.is_dir()):
            path = mounted
    if in_container() and not any(
        point == path or point in path.parents for _, point in bind_mounts()
    ):
        # Outside a mounted volume, files vanish when the container is rebuilt.
        raise ValueError(f"{value} isn’t on this server.{where}")
    if not path.is_dir():
        if path.exists():
            raise ValueError(f"{value} is a file, not a folder.")
        try:
            path.mkdir(parents=True)
            # The household's group (SPARROW_MEDIA_GID) may manage it too.
            path.chmod(0o775)
        except OSError:
            raise ValueError(f"Sparrow can’t create {value}.{where}") from None
    if not os.access(path, os.R_OK | os.X_OK):
        raise ValueError(f"Sparrow can’t open {path}.")
    if writable and not os.access(path, os.W_OK):
        raise ValueError(f"Sparrow can’t save into {path}.")
    return str(path)


def settle_media_folders(config: SparrowConfig, before: SparrowConfig) -> None:
    """Make newly chosen folders exist where Sparrow can reach them."""
    for name, writable in (("library_dir", False), ("staging_dir", True)):
        value = getattr(config, name)
        # Saving a folder that has gone missing makes it again.
        if value and (value != getattr(before, name) or not Path(value).is_dir()):
            setattr(config, name, usable_media_folder(value, writable=writable))
    validate_media_roots(config)


def repair_media_folders(config: SparrowConfig) -> bool:
    """Point saved host paths at their mounted copies. Never creates anything:
    a missing folder may be an unplugged drive, not a folder to make."""
    changed = False
    for name in ("library_dir", "staging_dir"):
        value = getattr(config, name)
        if value and not Path(value).is_dir():
            mounted = mounted_path(value)
            if mounted and mounted.is_dir():
                setattr(config, name, str(mounted))
                changed = True
    if changed:
        validate_media_roots(config)
    return changed


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
