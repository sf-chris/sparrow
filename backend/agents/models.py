"""
v3 agentic domain models.

A Job is a contract against TMDB: exactly these episodes, this quality
window, this audio preference, this urgency. It is done when the library
inventory provably matches the spec — never when a status field flips.

An AgentSession is one LLM tool-use loop bound to a job (Fetch), a landed
download (Media), or a personal subscription (Librarian). Sessions hibernate
between turns and are woken by events or their own timers.

Watching is the product; the journal explains the work in plain language.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import time
import uuid


class Urgency(str, Enum):
    TONIGHT = "tonight"  # trade quality for swarm health
    SOON = "soon"  # balanced
    WHENEVER = "whenever"  # hold out for the preferred tier


class JobStatus(str, Enum):
    ACTIVE = "active"  # agent working or hibernating on a trigger
    COMPLETE = "complete"  # inventory provably matches the spec
    ABANDONED = "abandoned"  # agent concluded the content isn't out there
    PAUSED = "paused"  # user paused the job


class SessionStatus(str, Enum):
    RUNNING = "running"  # a turn is executing right now
    HIBERNATING = "hibernating"  # waiting on an event or wake_at timer
    CLOSED = "closed"


class CaseState(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    WAITING = "waiting"
    NEEDS_INPUT = "needs_input"
    BUDGET_LIMITED = "budget_limited"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentKind(str, Enum):
    FETCH = "fetch"
    MEDIA = "media"
    LIBRARIAN = "librarian"
    DISCOVERY = "discovery"
    SUBTITLE = "subtitle"


class MonitoringMode(str, Enum):
    """How much standing authority the user granted for a show.

    Owning episodes never implies permission to acquire more. Every
    acquisition must trace back to one of these explicit grants.
    """

    EXACT = "exact"  # the exact requested episodes, nothing else
    KEEP_CURRENT = "keep_current"  # exact request + new episodes as they air
    SEASONS = "seasons"  # the listed seasons only (past and future)
    BACKFILL = "backfill"  # explicit: everything available, historical included


def _uid() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Mandate:
    """The user's recorded authority for one title.

    Created/updated only by user actions (requesting, changing preferences) —
    never by an agent. The tool layer checks every librarian-spawned job
    against this record, so a model that reasons its way to "backfill
    everything" still cannot act on it.
    """

    tmdb_id: int = 0
    media_type: str = "tv"  # "tv" | "movie"
    mode: MonitoringMode = MonitoringMode.EXACT
    # Union of everything the user explicitly requested: {"1": [1, 2, ...]}.
    # Always permitted (re-downloads, upgrades) regardless of mode.
    requested_episodes: dict = field(default_factory=dict)
    # SEASONS mode: the season numbers the user opted into.
    seasons: list = field(default_factory=list)
    # When the current mode was granted. KEEP_CURRENT only covers episodes
    # that aired on/after this moment.
    granted_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def merge_request(self, wanted_episodes: dict) -> None:
        """Record an explicit user request into the standing authority."""
        for season, episodes in (wanted_episodes or {}).items():
            have = set(self.requested_episodes.get(str(season), []))
            have.update(int(e) for e in episodes)
            self.requested_episodes[str(season)] = sorted(have)
        self.updated_at = time.time()

    def allows_episode(
        self, season: int, episode: int, aired_at: Optional[float] = None
    ) -> bool:
        """Deterministic authority check for one episode.

        aired_at is the episode's TMDB air date (unix ts) and is only
        consulted for KEEP_CURRENT; pass None when unknown/unaired.
        """
        if int(episode) in {
            int(e) for e in self.requested_episodes.get(str(int(season)), [])
        }:
            return True
        if self.mode == MonitoringMode.BACKFILL:
            return True
        if self.mode == MonitoringMode.SEASONS:
            return int(season) in {int(s) for s in self.seasons}
        if self.mode == MonitoringMode.KEEP_CURRENT:
            return aired_at is not None and aired_at >= self.granted_at
        return False  # EXACT: nothing beyond the recorded request

    def describe(self) -> str:
        """Plain-language contract line, e.g. for the show page header."""
        parts = []
        for season in sorted(self.requested_episodes, key=int):
            episodes = sorted({int(e) for e in self.requested_episodes[season]})
            if episodes:
                parts.append(
                    f"Season {season}, episode{'s' if len(episodes) > 1 else ''} "
                    + ", ".join(map(str, episodes))
                )
        requested = "; ".join(parts) or "Nothing yet"
        monitoring = {
            MonitoringMode.EXACT: "Off",
            MonitoringMode.KEEP_CURRENT: "New episodes as they air",
            MonitoringMode.SEASONS: (
                "Seasons "
                + ", ".join(str(s) for s in sorted(int(x) for x in self.seasons))
                if self.seasons
                else "Off"
            ),
            MonitoringMode.BACKFILL: "Everything available",
        }[self.mode]
        return f"Requested: {requested} · Future-season monitoring: {monitoring}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Mandate":
        d = dict(d)
        d["mode"] = MonitoringMode(d.get("mode", "exact"))
        return cls(**d)


@dataclass
class Job:
    """A user (or librarian) request compiled into a TMDB contract."""

    id: str = field(default_factory=_uid)
    tmdb_id: int = 0
    media_type: str = "tv"  # "tv" | "movie"
    title: str = ""
    year: Optional[int] = None
    poster_path: str = ""
    # The contract. wanted_episodes: {"1": [1, 2, ...]} per season; empty for
    # movies. Episode lists come from TMDB at job creation and may be extended
    # by the Fetch Agent if TMDB adds episodes (e.g. a season still airing).
    wanted_episodes: dict = field(default_factory=dict)
    preferred_quality: str = "1080p"
    min_quality: str = "720p"
    audio_pref: str = "any"  # e.g. "any", "english", "original"
    urgency: Urgency = Urgency.SOON
    status: JobStatus = JobStatus.ACTIVE
    origin: str = "user"  # "user" | "librarian" | "upgrade"
    user_id: str = ""
    library_id: str = "local"
    node_id: str = ""
    preferences: dict = field(default_factory=dict)
    revision: int = 1  # invalidates already-running tool contexts
    original_language: str = ""
    # Plain-language one-liner for cards ("waiting for Friday's episode").
    state_line: str = ""
    next_wake_at: float = 0.0  # mirrored from the session, for UI countdowns
    session_id: str = ""  # the Fetch Agent session that owns this job
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None

    def episode_count(self) -> int:
        return sum(len(eps) for eps in self.wanted_episodes.values())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["urgency"] = self.urgency.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        d = dict(d)
        d["urgency"] = Urgency(d.get("urgency", "soon"))
        d["status"] = JobStatus(d.get("status", "active"))
        return cls(**d)


@dataclass
class JournalEntry:
    """One plain-language note from an agent. Rendered verbatim in the UI —
    no hashes, seeders, codecs, or release names."""

    id: str = field(default_factory=_uid)
    job_id: str = ""
    session_id: str = ""
    agent: str = "fetch"  # AgentKind value
    ts: float = field(default_factory=time.time)
    text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "JournalEntry":
        return cls(**d)


@dataclass
class Spend:
    """What the agent has cost so far — shown to the agent itself every turn
    so it can act like an adult about persistence."""

    turns: int = 0
    searches: int = 0
    peeks: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    dollars: float = 0.0
    entries: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Spend":
        return cls(
            **{
                k: d.get(k, 0)
                for k in (
                    "turns",
                    "searches",
                    "peeks",
                    "input_tokens",
                    "output_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                    "dollars",
                )
            },
            entries=list(d.get("entries") or []),
        )


@dataclass
class AgentSession:
    """One persistent LLM tool-use loop. The full message history is stored so
    the session resumes with all its context when an event wakes it."""

    id: str = field(default_factory=_uid)
    agent: AgentKind = AgentKind.FETCH
    user_id: str = ""
    job_id: str = ""  # empty for the librarian
    job_revision: int = 1
    download_id: str = ""  # media agent: the landed download
    model: str = ""  # current model (media agent can self-escalate)
    status: SessionStatus = SessionStatus.HIBERNATING
    messages: list = field(default_factory=list)  # Anthropic message dicts
    wake_at: float = 0.0  # 0 = no timer; woken by events only
    wake_reason: str = ""  # plain language: why/when it expects to wake
    spend: Spend = field(default_factory=Spend)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None
    close_reason: str = ""
    outcome: CaseState = CaseState.WAITING
    budget_scope: str = ""  # stable across replacement subscription sessions

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent": self.agent.value,
            "user_id": self.user_id,
            "job_id": self.job_id,
            "job_revision": self.job_revision,
            "download_id": self.download_id,
            "model": self.model,
            "status": self.status.value,
            "messages": self.messages,
            "wake_at": self.wake_at,
            "wake_reason": self.wake_reason,
            "spend": self.spend.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "close_reason": self.close_reason,
            "outcome": self.outcome.value,
            "budget_scope": self.budget_scope,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentSession":
        d = dict(d)
        d["agent"] = AgentKind(d.get("agent", "fetch"))
        d["status"] = SessionStatus(d.get("status", "hibernating"))
        d["spend"] = Spend.from_dict(d.get("spend") or {})
        d["outcome"] = CaseState(d.get("outcome", "waiting"))
        return cls(**d)


@dataclass
class Event:
    """A plumbing event that wakes agent sessions. Plumbing makes zero
    decisions — it only describes what happened."""

    kind: str  # download_stalled, files_landed, client_recovered,
    # episode_aired, media_report, timer, nudge, job_created
    job_id: str = ""
    download_id: str = ""
    session_id: str = ""  # target a specific session (else routed by job/kind)
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=_uid)

    def to_dict(self) -> dict:
        return asdict(self)
