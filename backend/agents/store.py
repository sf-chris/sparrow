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
from contextlib import contextmanager
import time
from pathlib import Path
from typing import Optional

from .models import (
    Job,
    JournalEntry,
    AgentSession,
    JobStatus,
    Mandate,
    SessionStatus,
    AgentKind,
    Event,
)


class AgentStore:
    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        from .migrations import prepare

        prepare(self.data_dir)
        self._db_path = self.data_dir / "sparrow.db"
        self.memory_dir = self.data_dir / "memory"
        (self.memory_dir / "shows").mkdir(parents=True, exist_ok=True)
        self._init_db()
        from .operations import Operations

        self.operations = Operations(self.data_dir)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

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
                CREATE TABLE IF NOT EXISTS mandates (
                    tmdb_id INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    data TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (tmdb_id, media_type)
                );
                CREATE TABLE IF NOT EXISTS agent_events (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, routed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS agent_deliveries (
                    session_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    data TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(session_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS agent_invocations (
                    session_id TEXT NOT NULL, tool_id TEXT NOT NULL,
                    name TEXT NOT NULL, arguments TEXT NOT NULL,
                    revision INTEGER NOT NULL, result TEXT, control TEXT,
                    PRIMARY KEY(session_id, tool_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_events_pending ON agent_events(routed);
                CREATE INDEX IF NOT EXISTS idx_agent_deliveries_pending ON agent_deliveries(session_id,acknowledged);
            """)

    # ─── Jobs ────────────────────────────────────────────────────────────

    def save_job(self, job: Job) -> Job:
        job.updated_at = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT status FROM jobs WHERE id=?", (job.id,)
            ).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO jobs (id, data, tmdb_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    job.id,
                    json.dumps(job.to_dict()),
                    job.tmdb_id,
                    job.status.value,
                    job.created_at,
                    job.updated_at,
                ),
            )
            from .operations import EVENTS

            code = (
                "request_submitted" if not previous else "request_" + job.status.value
            )
            if (
                not previous or previous["status"] != job.status.value
            ) and code in EVENTS:
                self.operations.job(code, job, db=conn)
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return Job.from_dict(json.loads(row["data"])) if row else None

    def get_jobs(
        self, status: Optional[JobStatus] = None, tmdb_id: Optional[int] = None
    ) -> list[Job]:
        q, params = "SELECT data FROM jobs", []
        clauses = []
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if tmdb_id:
            clauses.append("tmdb_id = ?")
            params.append(tmdb_id)
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

    # ─── Mandates (user authority; agents read, never write) ────────────

    def save_mandate(self, mandate: Mandate) -> Mandate:
        mandate.updated_at = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mandates (tmdb_id, media_type, data, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    mandate.tmdb_id,
                    mandate.media_type,
                    json.dumps(mandate.to_dict()),
                    mandate.updated_at,
                ),
            )
        return mandate

    def get_mandate(self, tmdb_id: int, media_type: str = "tv") -> Optional[Mandate]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM mandates WHERE tmdb_id = ? AND media_type = ?",
                (tmdb_id, media_type),
            ).fetchone()
        return Mandate.from_dict(json.loads(row["data"])) if row else None

    def get_mandates(self) -> list[Mandate]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM mandates ORDER BY updated_at DESC"
            ).fetchall()
        return [Mandate.from_dict(json.loads(r["data"])) for r in rows]

    # ─── Journal ─────────────────────────────────────────────────────────

    def add_journal(self, entry: JournalEntry) -> JournalEntry:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO journal_entries (id, job_id, session_id, agent, ts, text) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.id,
                    entry.job_id,
                    entry.session_id,
                    entry.agent,
                    entry.ts,
                    entry.text,
                ),
            )
        return entry

    def get_journal(self, job_id: str = "", limit: int = 200) -> list[JournalEntry]:
        """Entries for one job (or all, for the Activity console), oldest first."""
        with self._connect() as conn:
            if job_id:
                rows = conn.execute(
                    "SELECT * FROM journal_entries WHERE job_id = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (job_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM journal_entries ORDER BY ts DESC LIMIT ?", (limit,)
                ).fetchall()
        entries = [
            JournalEntry(
                id=r["id"],
                job_id=r["job_id"],
                session_id=r["session_id"],
                agent=r["agent"],
                ts=r["ts"],
                text=r["text"],
            )
            for r in rows
        ]
        return list(reversed(entries))

    # ─── Sessions ────────────────────────────────────────────────────────

    def save_session(self, s: AgentSession) -> AgentSession:
        s.updated_at = time.time()
        with self._connect() as conn:
            self._save_session(conn, s)
        return s

    def _save_session(self, conn, s):
        conn.execute(
            "INSERT OR REPLACE INTO agent_sessions "
            "(id, data, agent, job_id, status, wake_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                s.id,
                json.dumps(s.to_dict()),
                s.agent.value,
                s.job_id,
                s.status.value,
                s.wake_at,
                s.updated_at,
            ),
        )

    def enqueue_event(self, event: Event) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO agent_events(id,data) VALUES(?,?)",
                (event.id, json.dumps(event.to_dict())),
            )

    def unrouted_events(self) -> list[Event]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT data FROM agent_events WHERE routed=0 ORDER BY rowid"
            ).fetchall()
        return [Event(**json.loads(row["data"])) for row in rows]

    def mark_routed(self, event: Event) -> None:
        with self._connect() as db:
            db.execute("UPDATE agent_events SET routed=1 WHERE id=?", (event.id,))

    def enqueue_delivery(self, session_id: str, event: Event) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO agent_deliveries(session_id,event_id,data) VALUES(?,?,?)",
                (session_id, event.id, json.dumps(event.to_dict())),
            )

    def pending_events(self, session_id: str) -> list[Event]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT data FROM agent_deliveries WHERE session_id=? AND acknowledged=0 ORDER BY rowid",
                (session_id,),
            ).fetchall()
        return [Event(**json.loads(row["data"])) for row in rows]

    def pending_session_ids(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT DISTINCT session_id FROM agent_deliveries WHERE acknowledged=0"
            ).fetchall()
        return [row["session_id"] for row in rows]

    def acknowledge_events(self, session: AgentSession, events: list[Event]) -> None:
        """Acknowledge only in the same commit that records evidence in context."""
        session.updated_at = time.time()
        with self._connect() as db:
            self._save_session(db, session)
            db.executemany(
                "UPDATE agent_deliveries SET acknowledged=1 WHERE session_id=? AND event_id=?",
                [(session.id, event.id) for event in events],
            )

    def invocation(self, session_id: str, tool_id: str):
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM agent_invocations WHERE session_id=? AND tool_id=?",
                (session_id, tool_id),
            ).fetchone()
        if not row:
            return None
        return {
            **dict(row),
            "arguments": json.loads(row["arguments"]),
            "result": json.loads(row["result"]) if row["result"] else None,
            "control": json.loads(row["control"]) if row["control"] else {},
        }

    def start_invocation(
        self, session: AgentSession, tool_id: str, name: str, args: dict
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO agent_invocations(session_id,tool_id,name,arguments,revision) VALUES(?,?,?,?,?)",
                (session.id, tool_id, name, json.dumps(args), session.job_revision),
            )

    def finish_invocation(
        self, session: AgentSession, tool_id: str, result: dict, control: dict
    ) -> None:
        with self._connect() as db:
            self._save_session(db, session)
            db.execute(
                "UPDATE agent_invocations SET result=?,control=? WHERE session_id=? AND tool_id=?",
                (json.dumps(result), json.dumps(control), session.id, tool_id),
            )

    def get_session(self, session_id: str) -> Optional[AgentSession]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM agent_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return AgentSession.from_dict(json.loads(row["data"])) if row else None

    def get_sessions(
        self,
        agent: Optional[AgentKind] = None,
        job_id: str = "",
        open_only: bool = False,
    ) -> list[AgentSession]:
        q, params = "SELECT data FROM agent_sessions", []
        clauses = []
        if agent:
            clauses.append("agent = ?")
            params.append(agent.value)
        if job_id:
            clauses.append("job_id = ?")
            params.append(job_id)
        if open_only:
            clauses.append("status != ?")
            params.append(SessionStatus.CLOSED.value)
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
                (SessionStatus.HIBERNATING.value, now),
            ).fetchall()
        return [AgentSession.from_dict(json.loads(r["data"])) for r in rows]

    # ─── Memory (plain markdown notes) ───────────────────────────────────

    def _memory_path(
        self, scope: str, tmdb_id: Optional[int] = None, user_id: str = ""
    ) -> Path:
        import hashlib

        root = self.memory_dir
        if user_id:
            root = root / "people" / hashlib.sha256(user_id.encode()).hexdigest()
            (root / "shows").mkdir(parents=True, exist_ok=True)
        if scope == "global":
            return root / "global.md"
        if scope == "show" and tmdb_id:
            return root / "shows" / f"{int(tmdb_id)}.md"
        raise ValueError(f"bad memory scope {scope!r} (tmdb_id={tmdb_id})")

    def read_memory(
        self, scope: str, tmdb_id: Optional[int] = None, user_id: str = ""
    ) -> str:
        path = self._memory_path(scope, tmdb_id, user_id)
        return path.read_text() if path.exists() else ""

    def write_memory(
        self, scope: str, content: str, tmdb_id: Optional[int] = None, user_id: str = ""
    ) -> None:
        # Keep notes bounded — memory is lessons, not a transcript.
        content = content[-16000:]
        path = self._memory_path(scope, tmdb_id, user_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content)
        temporary.replace(path)
