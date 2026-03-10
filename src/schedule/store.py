"""SQLite-backed persistent cron job registry."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class ScheduleStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_table()

    def _create_table(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                job_id     TEXT PRIMARY KEY,
                cron       TEXT NOT NULL,
                prompt     TEXT NOT NULL,
                enabled    INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_run   TEXT,
                run_count  INTEGER NOT NULL DEFAULT 0
            );
        """)
        self._conn.commit()

    def upsert(
        self, job_id: str, cron: str, prompt: str, enabled: bool = True
    ) -> None:
        self._conn.execute(
            """INSERT INTO scheduled_jobs (job_id, cron, prompt, enabled)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                   cron=excluded.cron,
                   prompt=excluded.prompt,
                   enabled=excluded.enabled""",
            (job_id, cron, prompt, int(enabled)),
        )
        self._conn.commit()

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_enabled(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM scheduled_jobs WHERE enabled = 1 ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM scheduled_jobs ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        if self.get(job_id) is None:
            return False
        self._conn.execute(
            "UPDATE scheduled_jobs SET enabled = ? WHERE job_id = ?",
            (int(enabled), job_id),
        )
        self._conn.commit()
        return True

    def remove(self, job_id: str) -> bool:
        if self.get(job_id) is None:
            return False
        self._conn.execute(
            "DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        )
        self._conn.commit()
        return True

    def record_run(self, job_id: str) -> None:
        self._conn.execute(
            """UPDATE scheduled_jobs
               SET last_run = datetime('now'), run_count = run_count + 1
               WHERE job_id = ?""",
            (job_id,),
        )
        self._conn.commit()
