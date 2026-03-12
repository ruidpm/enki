"""Audit database — SQLite-backed, tiered storage."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from .events import Tier1Event, Tier2Event
from .integrity import compute_chain_hash, compute_data_hash

log = structlog.get_logger()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tier1 (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    data        TEXT NOT NULL,
    data_hash   TEXT NOT NULL,
    prev_chain_hash TEXT NOT NULL DEFAULT '',
    chain_hash  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tier2 (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    data        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS tier1_session ON tier1(session_id);
CREATE INDEX IF NOT EXISTS tier1_timestamp ON tier1(timestamp);
CREATE INDEX IF NOT EXISTS tier2_session ON tier2(session_id);
CREATE INDEX IF NOT EXISTS tier2_timestamp ON tier2(timestamp);
"""


class AuditDB:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._tier1_lock = asyncio.Lock()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _last_chain_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute("SELECT chain_hash FROM tier1 ORDER BY id DESC LIMIT 1").fetchone()
        return row["chain_hash"] if row else ""

    async def log_tier1(self, event_type: Tier1Event, session_id: str, data: dict[str, Any]) -> None:
        timestamp = datetime.now(UTC).isoformat()
        full_data = {"event_type": event_type, "session_id": session_id, "timestamp": timestamp, **data}
        data_hash = compute_data_hash(full_data)
        async with self._tier1_lock:
            with self._conn() as conn:
                prev_hash = self._last_chain_hash(conn)
                chain_hash = compute_chain_hash(prev_hash, data_hash)
                conn.execute(
                    """INSERT INTO tier1
                       (event_type, session_id, timestamp, data, data_hash, prev_chain_hash, chain_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (event_type, session_id, timestamp, json.dumps(data, default=str), data_hash, prev_hash, chain_hash),
                )

    async def log_tier2(self, event_type: Tier2Event, session_id: str, data: dict[str, Any]) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tier2 (event_type, session_id, timestamp, data) VALUES (?, ?, ?, ?)",
                (event_type, session_id, timestamp, json.dumps(data, default=str)),
            )

    async def log_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any],
        allowed: bool,
        block_reason: str | None,
        session_id: str,
    ) -> None:
        if not allowed:
            await self.log_tier1(
                Tier1Event.GUARDRAIL_BLOCK,
                session_id,
                {"tool": tool_name, "reason": block_reason, "params_hash": compute_data_hash(params)},
            )
        else:
            await self.log_tier2(
                Tier2Event.TOOL_CALLED,
                session_id,
                {"tool": tool_name, "params": params},
            )

    def purge_old_tier2(self, days: int = 30) -> int:
        """Delete Tier 2 records older than `days`. Returns count deleted."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM tier2 WHERE timestamp < ?", (cutoff,))
            return cur.rowcount
