"""Workspace registry — SQLite-backed store for external project workspaces."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class TrustLevel:
    """Graduated autonomy levels for a workspace.

    READ_ONLY   (0) — Enki can read/analyse only; no writes.
    PROPOSE     (1) — Write locally; all git ops need confirmation. (default)
    AUTO_COMMIT (2) — Auto-commit to feature branches; confirm push.
    AUTO_PUSH   (3) — Auto-push feature branches; confirm PR creation.
    TRUSTED     (4) — Auto-create PRs; user reviews on GitHub only.
    """

    READ_ONLY: int = 0
    PROPOSE: int = 1
    AUTO_COMMIT: int = 2
    AUTO_PUSH: int = 3
    TRUSTED: int = 4

    ALL: frozenset[int] = frozenset({0, 1, 2, 3, 4})


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id      TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    local_path        TEXT NOT NULL,
    git_remote        TEXT,
    language          TEXT,
    description       TEXT,
    trust_level       INTEGER NOT NULL DEFAULT 1,
    github_token_env  TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    last_used         TEXT
);
"""


class WorkspaceStore:
    """Persistent registry of external project workspaces."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(
        self,
        workspace_id: str,
        *,
        name: str,
        local_path: str,
        git_remote: str | None = None,
        language: str | None = None,
        description: str | None = None,
        trust_level: int = TrustLevel.PROPOSE,
        github_token_env: str | None = None,
    ) -> None:
        """Insert or replace a workspace record."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO workspaces
                (workspace_id, name, local_path, git_remote, language,
                 description, trust_level, github_token_env)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id, name, local_path, git_remote, language,
                description, trust_level, github_token_env,
            ),
        )
        self._conn.commit()

    def remove(self, workspace_id: str) -> bool:
        """Remove a workspace. Returns False if not found."""
        cur = self._conn.execute(
            "DELETE FROM workspaces WHERE workspace_id = ?", (workspace_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def update_trust(self, workspace_id: str, trust_level: int) -> bool:
        """Update trust level. Returns False if workspace not found."""
        cur = self._conn.execute(
            "UPDATE workspaces SET trust_level = ? WHERE workspace_id = ?",
            (trust_level, workspace_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def touch(self, workspace_id: str) -> None:
        """Update last_used timestamp. No-op if workspace not found."""
        self._conn.execute(
            "UPDATE workspaces SET last_used = datetime('now') WHERE workspace_id = ?",
            (workspace_id,),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, workspace_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM workspaces ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
