"""
Sparrow data models — the domain objects that flow through the system.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Any
import json
import time


# ─── Enums ───────────────────────────────────────────────────────────────────

class MediaType(str, Enum):
    MOVIE = "movie"
    TV = "tv"
    UNKNOWN = "unknown"


class Quality(str, Enum):
    Q_2160P = "2160p"
    Q_1080P = "1080p"
    Q_720P = "720p"
    Q_480P = "480p"
    ANY = "any"


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    SEEDING = "seeding"
    COMPLETED = "completed"
    ORGANIZING = "organizing"
    ORGANIZED = "organized"
    ERROR = "error"
    PAUSED = "paused"


class TorrentClientType(str, Enum):
    QBITTORRENT = "qbittorrent"
    TRANSMISSION = "transmission"
    NONE = "none"


class RequestStatus(str, Enum):
    PENDING = "pending"
    RESOLVING = "resolving"
    EVALUATING = "evaluating"
    BLOCKED = "blocked"
    DOWNLOADING = "downloading"
    ORGANIZING = "organizing"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    SEARCHING = "searching"   # curator is actively looking for releases
    WAITING = "waiting"       # curator is on cooldown before the next attempt


# Quality ladder used for cascade/upgrade decisions. Higher rank = better.
QUALITY_RANK: dict[str, int] = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4}


def quality_rank(q: str) -> int:
    return QUALITY_RANK.get((q or "").lower(), 0)


class RequestStrategy(str, Enum):
    UNKNOWN = "unknown"
    MOVIE = "movie"
    SEASON_PACK = "season_pack"
    EPISODES = "episodes"
    SERIES = "series"


# ─── Config ──────────────────────────────────────────────────────────────────

@dataclass
class TorrentClientConfig:
    type: TorrentClientType = TorrentClientType.NONE
    host: str = "localhost"
    port: int = 8080
    username: str = ""
    password: str = ""
    url: str = ""  # override full URL if needed

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TorrentClientConfig":
        d = dict(d)
        d.pop("password_configured", None)
        d.pop("url_configured", None)
        d["type"] = TorrentClientType(d.get("type", "none"))
        return cls(**d)


@dataclass
class SparrowConfig:
    staging_dir: str = ""
    library_dir: str = ""
    torrent_client: TorrentClientConfig = field(default_factory=TorrentClientConfig)
    quality_preference: Quality = Quality.Q_1080P
    tmdb_api_key: str = ""
    anthropic_api_key: str = ""
    onboarding_complete: bool = False
    auto_organize: bool = True
    preferred_search_engines: list[str] = field(default_factory=lambda: ["apibay"])
    seeding_ratio_limit: float = 2.0   # stop seeding at this ratio (0 = no limit)
    seeding_time_hours: float = 0.0    # stop seeding after N hours (0 = no limit)
    prefer_smaller_files: bool = False  # prefer smaller encodes (x265/HEVC) over large ones
    prefer_season_packs: bool = True    # prefer full-season packs when they score well
    season_pack_size_limit_gb: float = 0.0  # 0 = automatic size sanity by quality/episode count
    max_active_transfers: int = 3       # hard cap on concurrent agent-managed transfers
    smart_model: str = "claude-sonnet-5"
    cheap_model: str = "claude-haiku-4-5"

    def to_dict(self) -> dict:
        return {
            "staging_dir": self.staging_dir,
            "library_dir": self.library_dir,
            "torrent_client": self.torrent_client.to_dict(),
            "quality_preference": self.quality_preference.value,
            "tmdb_api_key": self.tmdb_api_key,
            "anthropic_api_key": self.anthropic_api_key,
            "onboarding_complete": self.onboarding_complete,
            "auto_organize": self.auto_organize,
            "preferred_search_engines": self.preferred_search_engines,
            "seeding_ratio_limit": self.seeding_ratio_limit,
            "seeding_time_hours": self.seeding_time_hours,
            "prefer_smaller_files": self.prefer_smaller_files,
            "prefer_season_packs": self.prefer_season_packs,
            "season_pack_size_limit_gb": self.season_pack_size_limit_gb,
            "max_active_transfers": self.max_active_transfers,
            "smart_model": self.smart_model,
            "cheap_model": self.cheap_model,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SparrowConfig":
        d = dict(d)
        if "torrent_client" in d and isinstance(d["torrent_client"], dict):
            d["torrent_client"] = TorrentClientConfig.from_dict(d["torrent_client"])
        if "quality_preference" in d:
            d["quality_preference"] = Quality(d["quality_preference"])
        d.setdefault("seeding_ratio_limit", 2.0)
        d.setdefault("seeding_time_hours", 0.0)
        d.setdefault("prefer_smaller_files", False)
        d.setdefault("prefer_season_packs", True)
        d.setdefault("season_pack_size_limit_gb", 0.0)
        d.setdefault("max_active_transfers", 3)
        d.setdefault("smart_model", "claude-sonnet-5")
        d.setdefault("cheap_model", "claude-haiku-4-5")
        return cls(**d)


# ─── Search ──────────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    id: str
    name: str
    info_hash: str
    seeders: int
    leechers: int
    size_bytes: int
    added_ts: int
    category: str
    source: str
    imdb_id: Optional[str] = None
    uploader: str = ""
    magnet_url: str = ""

    @property
    def size_human(self) -> str:
        b = self.size_bytes
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "info_hash": self.info_hash,
            "seeders": self.seeders,
            "leechers": self.leechers,
            "size_bytes": self.size_bytes,
            "size_human": self.size_human,
            "added_ts": self.added_ts,
            "category": self.category,
            "source": self.source,
            "imdb_id": self.imdb_id,
            "uploader": self.uploader,
            "magnet_url": self.magnet_url,
        }


# ─── Downloads ───────────────────────────────────────────────────────────────

@dataclass
class Download:
    id: str
    name: str
    magnet_url: str
    media_type: MediaType = MediaType.UNKNOWN
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0
    size_bytes: int = 0
    downloaded_bytes: int = 0
    download_speed: int = 0  # bytes/sec
    eta_seconds: int = -1
    torrent_hash: str = ""
    staging_path: str = ""
    library_path: str = ""
    tmdb_id: Optional[int] = None
    metadata: dict = field(default_factory=dict)
    added_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error_message: str = ""
    quality: str = ""
    # When progress/speed/eta were last confirmed against the client. The UI
    # uses this to show "updated 12s ago" and to flag stale numbers honestly.
    stats_updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "magnet_url": self.magnet_url,
            "media_type": self.media_type.value,
            "status": self.status.value,
            "progress": self.progress,
            "size_bytes": self.size_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "download_speed": self.download_speed,
            "eta_seconds": self.eta_seconds,
            "torrent_hash": self.torrent_hash,
            "staging_path": self.staging_path,
            "library_path": self.library_path,
            "tmdb_id": self.tmdb_id,
            "metadata": self.metadata,
            "added_at": self.added_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "quality": self.quality,
            "stats_updated_at": self.stats_updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Download":
        d = dict(d)
        d["media_type"] = MediaType(d.get("media_type", "unknown"))
        d["status"] = DownloadStatus(d.get("status", "queued"))
        return cls(**d)


# ─── Library ─────────────────────────────────────────────────────────────────

@dataclass
class LibraryItem:
    id: str
    title: str
    media_type: MediaType
    path: str
    year: Optional[int] = None
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    overview: str = ""
    poster_path: str = ""
    backdrop_path: str = ""
    genres: list[str] = field(default_factory=list)
    rating: Optional[float] = None
    seasons: Optional[int] = None       # TV only
    episode_count: Optional[int] = None  # TV only
    size_bytes: int = 0
    added_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)
    # TV only: per-episode inventory keyed by season then episode (string keys
    # for JSON stability): {"1": {"3": {"quality": "1080p", "path": ...,
    # "size_bytes": N, "group": "...", "added_at": ts, "verified": true}}}
    episodes: dict = field(default_factory=dict)

    def have_episodes(self, season: int) -> set[int]:
        return {int(e) for e in self.episodes.get(str(season), {})}

    def episode_file(self, season: int, episode: int) -> Optional[dict]:
        return self.episodes.get(str(season), {}).get(str(episode))

    def set_episode_file(self, season: int, episode: int, info: dict) -> None:
        self.episodes.setdefault(str(season), {})[str(episode)] = info

    def season_summary(self) -> dict[str, dict]:
        """Per-season counts + lowest quality, for coverage displays."""
        out: dict[str, dict] = {}
        for s, eps in self.episodes.items():
            qualities = [e.get("quality", "unknown") for e in eps.values()]
            ranked = [q for q in qualities if quality_rank(q)]
            out[s] = {
                "have": len(eps),
                "lowest_quality": min(ranked, key=quality_rank) if ranked else "unknown",
            }
        return out

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "media_type": self.media_type.value,
            "path": self.path,
            "year": self.year,
            "tmdb_id": self.tmdb_id,
            "imdb_id": self.imdb_id,
            "overview": self.overview,
            "poster_path": self.poster_path,
            "backdrop_path": self.backdrop_path,
            "genres": self.genres,
            "rating": self.rating,
            "seasons": self.seasons,
            "episode_count": self.episode_count,
            "size_bytes": self.size_bytes,
            "added_at": self.added_at,
            "metadata": self.metadata,
            "episodes": self.episodes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LibraryItem":
        d = dict(d)
        d["media_type"] = MediaType(d.get("media_type", "unknown"))
        d.setdefault("episodes", {})
        return cls(**d)


# ─── Requests ────────────────────────────────────────────────────────────────

@dataclass
class MediaRequest:
    id: str
    query: str
    status: RequestStatus = RequestStatus.PENDING
    strategy: RequestStrategy = RequestStrategy.UNKNOWN
    title: str = ""
    media_type: MediaType = MediaType.UNKNOWN
    season: Optional[int] = None
    episode_count: Optional[int] = None
    quality: str = "1080p"
    tmdb_id: Optional[int] = None
    progress_found: int = 0
    progress_total: int = 0
    decision_summary: str = ""
    error_message: str = ""
    download_ids: list[str] = field(default_factory=list)
    missing_episodes: list[int] = field(default_factory=list)
    evaluation: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Durable-goal fields — a request is a standing goal the curator keeps
    # working toward, not a one-shot job.
    # wanted_episodes: {"2": [1, 2, ..., 13]} — season -> episode numbers.
    # Empty for movies (the goal is the single title).
    wanted_episodes: dict = field(default_factory=dict)
    # min_quality: lowest quality we'll accept now; quality remains the
    # preferred target. If we settle below preferred, upgrade=True keeps the
    # curator watching for a better release.
    min_quality: str = "any"
    upgrade: bool = True
    # attempts: [{ts, info_hash, name, outcome: queued|failed|rejected, note}]
    attempts: list = field(default_factory=list)
    next_check_at: float = 0.0   # curator cooldown; 0 = check ASAP
    check_interval: float = 0.0  # current backoff interval in seconds
    paused: bool = False         # user paused the goal

    def tried_hashes(self) -> set[str]:
        return {a.get("info_hash", "").lower() for a in self.attempts if a.get("info_hash")}

    def record_attempt(self, info_hash: str, name: str, outcome: str, note: str = "") -> None:
        self.attempts.append({
            "ts": time.time(),
            "info_hash": (info_hash or "").lower(),
            "name": name,
            "outcome": outcome,
            "note": note,
        })
        self.attempts = self.attempts[-200:]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "query": self.query,
            "status": self.status.value,
            "strategy": self.strategy.value,
            "title": self.title,
            "media_type": self.media_type.value,
            "season": self.season,
            "episode_count": self.episode_count,
            "quality": self.quality,
            "tmdb_id": self.tmdb_id,
            "progress_found": self.progress_found,
            "progress_total": self.progress_total,
            "decision_summary": self.decision_summary,
            "error_message": self.error_message,
            "download_ids": self.download_ids,
            "missing_episodes": self.missing_episodes,
            "evaluation": self.evaluation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "wanted_episodes": self.wanted_episodes,
            "min_quality": self.min_quality,
            "upgrade": self.upgrade,
            "attempts": self.attempts,
            "next_check_at": self.next_check_at,
            "check_interval": self.check_interval,
            "paused": self.paused,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MediaRequest":
        d = dict(d)
        d["status"] = RequestStatus(d.get("status", "pending"))
        d["strategy"] = RequestStrategy(d.get("strategy", "unknown"))
        d["media_type"] = MediaType(d.get("media_type", "unknown"))
        d.setdefault("download_ids", [])
        d.setdefault("missing_episodes", [])
        d.setdefault("evaluation", {})
        d.setdefault("updated_at", d.get("created_at", time.time()))
        d.setdefault("wanted_episodes", {})
        d.setdefault("min_quality", "any")
        d.setdefault("upgrade", True)
        d.setdefault("attempts", [])
        d.setdefault("next_check_at", 0.0)
        d.setdefault("check_interval", 0.0)
        d.setdefault("paused", False)
        return cls(**d)


# ─── Torrent Client Info ──────────────────────────────────────────────────────

@dataclass
class TorrentClientInfo:
    type: TorrentClientType
    host: str
    port: int
    reachable: bool
    version: str = ""
    active_downloads: int = 0
    free_space_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "host": self.host,
            "port": self.port,
            "reachable": self.reachable,
            "version": self.version,
            "active_downloads": self.active_downloads,
            "free_space_bytes": self.free_space_bytes,
        }


# ─── CWM Log ─────────────────────────────────────────────────────────────────

@dataclass
class CWMLog:
    id: str
    timestamp: float
    event_type: str  # analysis, fix, suggestion, error
    summary: str
    detail: str = ""
    affected_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Activity Feed ───────────────────────────────────────────────────────────

@dataclass
class ActivityEvent:
    """A plain-language event for the user-facing activity feed.

    `message` must be written for a non-technical reader — no hashes, no
    seeder counts, no codec names. Technical context goes in `detail`.
    """
    id: str
    timestamp: float
    kind: str            # searching, found, downloading, organized, upgraded,
                         # gap, waiting, fixed, error
    message: str
    detail: str = ""
    request_id: str = ""
    tmdb_id: Optional[int] = None
    level: str = "info"  # info, success, warning, error

    def to_dict(self) -> dict:
        return asdict(self)
