"""
Persistence for the v3 agent system.

Jobs, journal entries, and agent sessions live in sqlite (same sparrow.db).
Agent memory is deliberately NOT in the database: it is plain markdown files
under data/memory/ (global.md + shows/<tmdb_id>.md) — notes, not code.
"""
from __future__ import annotations
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .models import Job, JournalEntry, AgentSession, JobStatus, SessionStatus, AgentKind


class AgentStore:
    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self._db_path = self.data_dir / "sparrow.db"
        self.memory_dir = self.data_dir / "memory"
        (self.memory_dir / "shows").mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    tmdb_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS journal_entries (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    ts REAL NOT NULL,
                    text TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_journal_job ON journal_entries (job_id, ts);
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    wake_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)

    # ─── Jobs ────────────────────────────────────────────────────────────

    def save_job(self, job: Job) -> Job:
        job.updated_at = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs (id, data, tmdb_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job.id, json.dumps(job.to_dict()), job.tmdb_id, job.status.value,
                 job.created_at, job.updated_at),
            )
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_dict(json.loads(row["data"])) if row else None

    def get_jobs(self, status: Optional[JobStatus] = None,
                 tmdb_id: Optional[int] = None) -> list[Job]:
        q, params = "SELECT data FROM jobs", []
        clauses = []
        if status:
            clauses.append("status = ?"); params.append(status.value)
        if tmdb_id:
            clauses.append("tmdb_id = ?"); params.append(tmdb_id)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(q, params).fetchall()
        return [Job.from_dict(json.loads(r["data"])) for r in rows]

    def delete_job(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.execute("DELETE FROM journal_entries WHERE job_id = ?", (job_id,))

    # ─── Journal ─────────────────────────────────────────────────────────

    def add_journal(self, entry: JournalEntry) -> JournalEntry:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO journal_entries (id, job_id, session_id, agent, ts, text) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (entry.id, entry.job_id, entry.session_id, entry.agent, entry.ts, entry.text),
            )
        return entry

    def get_journal(self, job_id: str = "", limit: int = 200) -> list[JournalEntry]:
        """Entries for one job (or all, for the Activity console), oldest first."""
        with self._connect() as conn:
            if job_id:
                rows = conn.execute(
                    "SELECT * FROM journal_entries WHERE job_id = ? "
                    "ORDER BY ts DESC LIMIT ?", (job_id, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM journal_entries ORDER BY ts DESC LIMIT ?",
                    (limit,)).fetchall()
        entries = [JournalEntry(id=r["id"], job_id=r["job_id"], session_id=r["session_id"],
                                agent=r["agent"], ts=r["ts"], text=r["text"]) for r in rows]
        return list(reversed(entries))

    # ─── Sessions ────────────────────────────────────────────────────────

    def save_session(self, s: AgentSession) -> AgentSession:
        s.updated_at = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agent_sessions "
                "(id, data, agent, job_id, status, wake_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (s.id, json.dumps(s.to_dict()), s.agent.value, s.job_id,
                 s.status.value, s.wake_at, s.updated_at),
            )
        return s

    def get_session(self, session_id: str) -> Optional[AgentSession]:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM agent_sessions WHERE id = ?",
                               (session_id,)).fetchone()
        return AgentSession.from_dict(json.loads(row["data"])) if row else None

    def get_sessions(self, agent: Optional[AgentKind] = None, job_id: str = "",
                     open_only: bool = False) -> list[AgentSession]:
        q, params = "SELECT data FROM agent_sessions", []
        clauses = []
        if agent:
            clauses.append("agent = ?"); params.append(agent.value)
        if job_id:
            clauses.append("job_id = ?"); params.append(job_id)
        if open_only:
            clauses.append("status != ?"); params.append(SessionStatus.CLOSED.value)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY updated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(q, params).fetchall()
        return [AgentSession.from_dict(json.loads(r["data"])) for r in rows]

    def due_sessions(self, now: Optional[float] = None) -> list[AgentSession]:
        """Hibernating sessions whose wake timer has expired."""
        now = now or time.time()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM agent_sessions WHERE status = ? AND wake_at > 0 AND wake_at <= ?",
                (SessionStatus.HIBERNATING.value, now)).fetchall()
        return [AgentSession.from_dict(json.loads(r["data"])) for r in rows]

    # ─── Memory (plain markdown notes) ───────────────────────────────────

    def _memory_path(self, scope: str, tmdb_id: Optional[int] = None) -> Path:
        if scope == "global":
            return self.memory_dir / "global.md"
        if scope == "show" and tmdb_id:
            return self.memory_dir / "shows" / f"{tmdb_id}.md"
        raise ValueError(f"bad memory scope {scope!r} (tmdb_id={tmdb_id})")

    def read_memory(self, scope: str, tmdb_id: Optional[int] = None) -> str:
        path = self._memory_path(scope, tmdb_id)
        return path.read_text() if path.exists() else ""

    def write_memory(self, scope: str, content: str, tmdb_id: Optional[int] = None) -> None:
        # Keep notes bounded — memory is lessons, not a transcript.
        content = content[-16000:]
        self._memory_path(scope, tmdb_id).write_text(content)
