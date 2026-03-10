"""Persistent memory store — SQLite + FTS5 + daily markdown logs."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Generator

import structlog

log = structlog.get_logger()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    log_date    TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    content,
    content='turns',
    content_rowid='id'
);

CREATE TABLE IF NOT EXISTS embeddings (
    id          INTEGER PRIMARY KEY,
    turn_id     INTEGER NOT NULL REFERENCES turns(id),
    embedding   BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fact        TEXT NOT NULL,
    source_date TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
"""


class MemoryStore:
    def __init__(
        self,
        db_path: Path,
        logs_dir: Path | None = None,
        facts_path: Path | None = None,
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._logs_dir = logs_dir
        self._facts_path = facts_path
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        timestamp: str | None = None,
    ) -> int:
        """Append a conversation turn. Returns the new row id."""
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        log_date = ts[:10]
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO turns (session_id, role, content, timestamp, log_date) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, ts, log_date),
            )
            row_id = int(cur.lastrowid or 0)

        # Also write to daily markdown log if configured
        if self._logs_dir is not None:
            self._logs_dir.mkdir(parents=True, exist_ok=True)
            log_file = self._logs_dir / f"{log_date}.md"
            time_str = ts[11:16]
            with log_file.open("a") as f:
                f.write(f"[{time_str}] {role.upper()}: {content}\n\n")

        return row_id

    def get_today_log_tail(self, n: int = 50) -> str:
        """Return the last n lines of today's daily log."""
        if self._logs_dir is None:
            return ""
        log_file = self._logs_dir / f"{date.today().isoformat()}.md"
        if not log_file.exists():
            return ""
        lines = log_file.read_text().splitlines()
        return "\n".join(lines[-n:])

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Strip FTS5 special characters and operators so raw user input doesn't cause syntax errors."""
        sanitized = re.sub(r'[^\w\s]', ' ', query)
        sanitized = re.sub(r'\b(AND|OR|NOT)\b', ' ', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'\s+', ' ', sanitized).strip()
        return sanitized

    def search_fts(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Full-text search over conversation turns."""
        safe_query = self._sanitize_fts_query(query)
        if not safe_query:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT t.id, t.session_id, t.role, t.content, t.timestamp
                   FROM turns t
                   JOIN turns_fts f ON t.id = f.rowid
                   WHERE turns_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_turns(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent turns for a session."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content, timestamp FROM turns "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return list(reversed([dict(r) for r in rows]))

    def add_fact(self, fact: str, source_date: str | None = None) -> None:
        """Store a distilled fact in SQLite (legacy — facts.md is preferred)."""
        sd = source_date or date.today().isoformat()
        created = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO facts (fact, source_date, created_at) VALUES (?, ?, ?)",
                (fact, sd, created),
            )

    def get_facts(self, limit: int = 50) -> list[str]:
        """Return most recent facts from SQLite."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT fact FROM facts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [r["fact"] for r in rows]

    def build_context(self, query: str, session_id: str, max_tokens: int = 2000) -> str:
        """
        Build memory context to inject into the system prompt.

        If facts_path is configured: read facts.md + today's log tail (no FTS).
        Otherwise: fall back to SQLite facts + FTS search results.
        """
        parts: list[str] = []

        if self._facts_path is not None:
            # New path: facts.md + daily log tail
            if self._facts_path.exists():
                facts_text = self._facts_path.read_text().strip()
                if facts_text:
                    parts.append(f"## User facts\n{facts_text}")

            log_tail = self.get_today_log_tail(n=50)
            if log_tail:
                parts.append(f"## Today's conversation log\n{log_tail}")
        else:
            # Legacy path: SQLite facts + FTS
            facts = self.get_facts(limit=20)
            if facts:
                parts.append("## Known facts\n" + "\n".join(f"- {f}" for f in facts))

            results = self.search_fts(query, limit=5)
            if results:
                hits = "\n".join(
                    f"[{r['timestamp'][:10]} {r['role']}]: {r['content'][:200]}"
                    for r in results
                )
                parts.append("## Relevant past context\n" + hits)

        context = "\n\n".join(parts)
        char_limit = max_tokens * 4
        if len(context) > char_limit:
            context = context[:char_limit] + "\n...[truncated]"
        return context
