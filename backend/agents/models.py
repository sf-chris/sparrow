"""
v3 agentic domain models.

A Job is a contract against TMDB: exactly these episodes, this quality
window, this audio preference, this urgency. It is done when the library
inventory provably matches the spec — never when a status field flips.

An AgentSession is one LLM tool-use loop bound to a job (Fetch), a landed
download (Media), or the whole library (Librarian). Sessions hibernate
between turns and are woken by events or their own timers.

The journal is the product: agents write plain-language reasoning as they
work, and the UI renders it verbatim.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import time
import uuid


class Urgency(str, Enum):
    TONIGHT = "tonight"      # trade quality for swarm health
    SOON = "soon"            # balanced
    WHENEVER = "whenever"    # hold out for the preferred tier


class JobStatus(str, Enum):
    ACTIVE = "active"        # agent working or hibernating on a trigger
    COMPLETE = "complete"    # inventory provably matches the spec
    ABANDONED = "abandoned"  # agent concluded the content isn't out there
    PAUSED = "paused"        # user paused the job


class SessionStatus(str, Enum):
    RUNNING = "running"          # a turn is executing right now
    HIBERNATING = "hibernating"  # waiting on an event or wake_at timer
    CLOSED = "closed"


class AgentKind(str, Enum):
    FETCH = "fetch"
    MEDIA = "media"
    LIBRARIAN = "librarian"


def _uid() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Job:
    """A user (or librarian) request compiled into a TMDB contract."""
    id: str = field(default_factory=_uid)
    tmdb_id: int = 0
    media_type: str = "tv"                 # "tv" | "movie"
    title: str = ""
    year: Optional[int] = None
    poster_path: str = ""
    # The contract. wanted_episodes: {"1": [1, 2, ...]} per season; empty for
    # movies. Episode lists come from TMDB at job creation and may be extended
    # by the Fetch Agent if TMDB adds episodes (e.g. a season still airing).
    wanted_episodes: dict = field(default_factory=dict)
    preferred_quality: str = "1080p"
    min_quality: str = "720p"
    audio_pref: str = "any"                # e.g. "any", "english", "original"
    urgency: Urgency = Urgency.SOON
    status: JobStatus = JobStatus.ACTIVE
    origin: str = "user"                   # "user" | "librarian" | "upgrade"
    # Plain-language one-liner for cards ("waiting for Friday's episode").
    state_line: str = ""
    next_wake_at: float = 0.0              # mirrored from the session, for UI countdowns
    session_id: str = ""                   # the Fetch Agent session that owns this job
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
    agent: str = "fetch"                   # AgentKind value
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
        return cls(**{k: d.get(k, 0) for k in
                      ("turns", "searches", "peeks", "input_tokens", "output_tokens",
                       "cache_creation_input_tokens", "cache_read_input_tokens", "dollars")},
                   entries=list(d.get("entries") or []))


@dataclass
class AgentSession:
    """One persistent LLM tool-use loop. The full message history is stored so
    the session resumes with all its context when an event wakes it."""
    id: str = field(default_factory=_uid)
    agent: AgentKind = AgentKind.FETCH
    job_id: str = ""                       # empty for the librarian
    download_id: str = ""                  # media agent: the landed download
    model: str = ""                        # current model (media agent can self-escalate)
    status: SessionStatus = SessionStatus.HIBERNATING
    messages: list = field(default_factory=list)   # Anthropic message dicts
    wake_at: float = 0.0                   # 0 = no timer; woken by events only
    wake_reason: str = ""                  # plain language: why/when it expects to wake
    spend: Spend = field(default_factory=Spend)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None
    close_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent": self.agent.value,
            "job_id": self.job_id,
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
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentSession":
        d = dict(d)
        d["agent"] = AgentKind(d.get("agent", "fetch"))
        d["status"] = SessionStatus(d.get("status", "hibernating"))
        d["spend"] = Spend.from_dict(d.get("spend") or {})
        return cls(**d)


@dataclass
class Event:
    """A plumbing event that wakes agent sessions. Plumbing makes zero
    decisions — it only describes what happened."""
    kind: str                              # download_stalled, files_landed, client_recovered,
                                           # episode_aired, media_report, timer, nudge, job_created
    job_id: str = ""
    download_id: str = ""
    session_id: str = ""                   # target a specific session (else routed by job/kind)
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)
