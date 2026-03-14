"""Workspace registry — SQLite-backed store for external project workspaces."""
from __future__ import annotations

import asyncio
import re
import sqlite3
from enum import IntEnum
from pathlib import Path
from typing import Any

# Patterns that indicate an actual token value (not an env var name)
_TOKEN_PREFIXES = ("ghp_", "gho_", "ghs_", "ghr_", "github_pat_")
# Env var names: uppercase/lowercase letters, digits, underscores — no spaces
_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TrustLevel(IntEnum):
    """Graduated autonomy levels for a workspace.

    READ_ONLY   (0) -- Enki can read/analyse only; no writes.
    PROPOSE     (1) -- Write locally; all git ops need confirmation. (default)
    AUTO_COMMIT (2) -- Auto-commit to feature branches; confirm push.
    AUTO_PUSH   (3) -- Auto-push feature branches; confirm PR creation.
    TRUSTED     (4) -- Auto-create PRs; user reviews on GitHub only.
    """

    READ_ONLY = 0
    PROPOSE = 1
    AUTO_COMMIT = 2
    AUTO_PUSH = 3
    TRUSTED = 4


# All valid trust level values — use this for validation instead of monkey-patching
ALL_TRUST_LEVELS: frozenset[int] = frozenset(TrustLevel)


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
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_github_token_env(value: str | None) -> None:
        """Ensure github_token_env is an env var name, not a raw token."""
        if value is None:
            return
        if any(value.startswith(prefix) for prefix in _TOKEN_PREFIXES):
            raise ValueError(
                f"github_token_env must be an env var name (e.g. 'GH_TOKEN'), "
                f"not a raw token value. Got a value starting with '{value[:6]}...'."
            )
        if not _ENV_VAR_RE.match(value):
            raise ValueError(
                f"github_token_env must be an env var name (letters, digits, underscores). "
                f"Got: '{value}'."
            )

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
        self._validate_github_token_env(github_token_env)
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

    # ------------------------------------------------------------------
    # Async wrappers — protect concurrent access with asyncio.Lock
    # ------------------------------------------------------------------

    async def add_async(
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
        async with self._lock:
            self.add(
                workspace_id, name=name, local_path=local_path,
                git_remote=git_remote, language=language, description=description,
                trust_level=trust_level, github_token_env=github_token_env,
            )

    async def remove_async(self, workspace_id: str) -> bool:
        async with self._lock:
            return self.remove(workspace_id)

    async def update_trust_async(self, workspace_id: str, trust_level: int) -> bool:
        async with self._lock:
            return self.update_trust(workspace_id, trust_level)

    async def touch_async(self, workspace_id: str) -> None:
        async with self._lock:
            self.touch(workspace_id)

    async def get_async(self, workspace_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return self.get(workspace_id)

    async def list_all_async(self) -> list[dict[str, Any]]:
        async with self._lock:
            return self.list_all()
