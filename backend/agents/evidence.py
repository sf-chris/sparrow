"""Immutable, session-private observations behind bounded tool-result previews."""

import hashlib
import json
import secrets
import time


INLINE_CHARS = 40_000
PAGE_CHARS = 6_000
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_SESSION_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024


class EvidenceArchive:
    def __init__(self, store):
        self.store = store
        with store._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_evidence (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    job_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    tool_id TEXT NOT NULL, tool_name TEXT NOT NULL,
                    source_is_error INTEGER NOT NULL,
                    observed_at REAL NOT NULL, sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, total_chars INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE(session_id, tool_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_evidence_session
                    ON agent_evidence(session_id, sequence);
            """)

    def capture(self, db, session, tool_id, result):
        """Called inside the invocation's write transaction, before its receipt."""
        content = result["content"]
        if len(content) <= INLINE_CHARS:
            return result
        encoded = content.encode("utf-8")
        size = len(encoded)
        used = db.execute(
            "SELECT COALESCE(SUM(size_bytes),0) AS total, "
            "COALESCE(SUM(CASE WHEN session_id=? THEN size_bytes ELSE 0 END),0) AS own "
            "FROM agent_evidence",
            (session.id,),
        ).fetchone()
        reason = ""
        if size > MAX_ARTIFACT_BYTES:
            reason = "This observation exceeds the per-artifact evidence limit."
        elif used["own"] + size > MAX_SESSION_BYTES:
            reason = "This session's evidence storage allowance is exhausted."
        elif used["total"] + size > MAX_TOTAL_BYTES:
            reason = "The server's evidence storage allowance is exhausted."
        if reason:
            # A tool effect may already have happened. Retain its control receipt
            # and explicitly distinguish missing evidence from a failed effect.
            return {
                **result,
                "is_error": True,
                "content": json.dumps(
                    {
                        "evidence_unavailable": reason,
                        "complete": False,
                        "total_chars": len(content),
                        "total_bytes": size,
                        "preview": content[:PAGE_CHARS],
                        "guidance": "The full observation was not retained. The tool's effect "
                        "may already have happened; inspect durable receipts before retrying. "
                        "Request narrower evidence. Do not infer missing items or completion "
                        "from this partial preview.",
                    },
                    ensure_ascii=False,
                ),
            }
        invocation = db.execute(
            "SELECT name,revision FROM agent_invocations WHERE session_id=? AND tool_id=?",
            (session.id, tool_id),
        ).fetchone()
        evidence_id = secrets.token_hex(16)
        db.execute(
            "INSERT INTO agent_evidence "
            "(id,session_id,user_id,job_id,revision,tool_id,tool_name,source_is_error,observed_at,"
            "sha256,size_bytes,total_chars,content) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                evidence_id,
                session.id,
                session.user_id,
                session.job_id,
                invocation["revision"],
                tool_id,
                invocation["name"],
                bool(result.get("is_error")),
                time.time(),
                hashlib.sha256(encoded).hexdigest(),
                size,
                len(content),
                content,
            ),
        )
        row = db.execute(
            "SELECT * FROM agent_evidence WHERE id=?", (evidence_id,)
        ).fetchone()
        return {
            **result,
            "content": json.dumps(self._page(row, 0, PAGE_CHARS), ensure_ascii=False),
        }

    @staticmethod
    def _metadata(row):
        return {
            "schema_version": 1,
            "evidence_id": row["id"],
            "tool_name": row["tool_name"],
            "tool_use_id": row["tool_id"],
            "source_is_error": bool(row["source_is_error"]),
            "observed_at": row["observed_at"],
            "request_revision": row["revision"],
            "sha256": row["sha256"],
            "total_bytes": row["size_bytes"],
            "total_chars": row["total_chars"],
            "artifact_complete": True,
        }

    @classmethod
    def _page(cls, row, offset, limit):
        end = min(offset + limit, row["total_chars"])
        return {
            **cls._metadata(row),
            "range_unit": "characters",
            "offset": offset,
            "end": end,
            "complete": offset == 0 and end == row["total_chars"],
            "next_offset": end if end < row["total_chars"] else None,
            "text": row["content"][offset:end],
            "guidance": "Use evidence_read with evidence_id and next_offset to retrieve "
            "the remaining text. Pages are slices of one immutable observation. "
            "Historical observations do not establish current authority or readiness.",
        }

    def read(self, session, evidence_id, offset=0, limit=PAGE_CHARS):
        if not isinstance(evidence_id, str) or not 1 <= len(evidence_id) <= 64:
            raise ValueError("Provide a valid evidence_id from this session.")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative character offset.")
        if type(limit) is not int or not 1 <= limit <= PAGE_CHARS:
            raise ValueError(f"limit must be between 1 and {PAGE_CHARS} characters.")
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM agent_evidence WHERE id=? AND session_id=? "
                "AND user_id=? AND job_id=?",
                (evidence_id, session.id, session.user_id, session.job_id),
            ).fetchone()
        if not row:
            raise ValueError("Evidence not found in this session.")
        if offset > row["total_chars"]:
            raise ValueError("offset exceeds the observation's total_chars.")
        return self._page(row, offset, limit)

    def list(self, session, after=0):
        if type(after) is not int or after < 0:
            raise ValueError("after must be a nonnegative evidence cursor.")
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT sequence,id,tool_name,tool_id,source_is_error,observed_at,revision,sha256,"
                "size_bytes,total_chars FROM agent_evidence WHERE session_id=? "
                "AND user_id=? AND job_id=? AND sequence>? ORDER BY sequence LIMIT 21",
                (session.id, session.user_id, session.job_id, after),
            ).fetchall()
        return {
            "observations": [self._metadata(row) for row in rows[:20]],
            "next_after": rows[19]["sequence"] if len(rows) > 20 else None,
            "scope": "Oversized tool results retained for this session only.",
        }
