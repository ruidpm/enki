"""Pipeline store — SQLite-backed persistence for structured engineering pipelines."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any


class PipelineStage:
    RESEARCH = "research"
    SCOPE = "scope"
    PLAN = "plan"
    IMPLEMENT = "implement"
    TEST = "test"
    REVIEW = "review"
    PR = "pr"

    ORDERED: list[str] = [RESEARCH, SCOPE, PLAN, IMPLEMENT, TEST, REVIEW, PR]

    @classmethod
    def next(cls, stage: str) -> str | None:
        try:
            idx = cls.ORDERED.index(stage)
            return cls.ORDERED[idx + 1] if idx + 1 < len(cls.ORDERED) else None
        except ValueError:
            return None


class PipelineStatus:
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"


_DDL = """
CREATE TABLE IF NOT EXISTS pipelines (
    pipeline_id   TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL,
    task          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    current_stage TEXT NOT NULL DEFAULT 'research',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pipeline_artifacts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id   TEXT NOT NULL,
    stage         TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (pipeline_id, stage)
);
"""


class PipelineStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def create(
        self,
        pipeline_id: str,
        *,
        workspace_id: str,
        task: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO pipelines (pipeline_id, workspace_id, task)
            VALUES (?, ?, ?)
            """,
            (pipeline_id, workspace_id, task),
        )
        self._conn.commit()

    def get(self, pipeline_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM pipelines WHERE pipeline_id = ?", (pipeline_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_active(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM pipelines WHERE status = 'active' ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM pipelines ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def advance_stage(self, pipeline_id: str, stage: str) -> None:
        self._conn.execute(
            """
            UPDATE pipelines
            SET current_stage = ?, updated_at = datetime('now')
            WHERE pipeline_id = ?
            """,
            (stage, pipeline_id),
        )
        self._conn.commit()

    def set_status(self, pipeline_id: str, status: str) -> None:
        self._conn.execute(
            """
            UPDATE pipelines
            SET status = ?, updated_at = datetime('now')
            WHERE pipeline_id = ?
            """,
            (status, pipeline_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def save_artifact(
        self,
        pipeline_id: str,
        stage: str,
        artifact_type: str,
        content: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO pipeline_artifacts (pipeline_id, stage, artifact_type, content)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (pipeline_id, stage) DO UPDATE SET
                artifact_type = excluded.artifact_type,
                content = excluded.content,
                created_at = datetime('now')
            """,
            (pipeline_id, stage, artifact_type, content),
        )
        self._conn.commit()

    def get_artifact(self, pipeline_id: str, stage: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM pipeline_artifacts WHERE pipeline_id = ? AND stage = ?",
            (pipeline_id, stage),
        ).fetchone()
        return dict(row) if row else None

    def list_artifacts(self, pipeline_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM pipeline_artifacts WHERE pipeline_id = ? ORDER BY id",
            (pipeline_id,),
        ).fetchall()
        return [dict(r) for r in rows]
