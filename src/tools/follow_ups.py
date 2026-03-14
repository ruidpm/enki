"""Follow-up tracking tool — CRUD via sqlite3 CLI."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

# Permitted CLI binary — enforced by code_scanner allowlist
_SQLITE = "sqlite3"

# Whitelist of valid status values for filtering
_VALID_STATUSES: frozenset[str] = frozenset({"open", "closed", "all"})

# Pattern for valid due_date: YYYY-MM-DD only
_DUE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _escape_sql_string(value: str) -> str:
    """Escape a string for safe inclusion in single-quoted SQL literal."""
    return value.replace("'", "''")


def _validate_int_id(value: Any) -> int | None:
    """Validate that value is (or can be safely parsed as) an integer."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


async def _run(db_path: Path, sql: str, json_mode: bool = False) -> str:
    args = [_SQLITE]
    if json_mode:
        args.append("-json")
    args += [str(db_path), sql]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"sqlite3 error: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def _ensure_schema(db_path: Path) -> None:
    schema = (
        "CREATE TABLE IF NOT EXISTS follow_ups ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "item TEXT NOT NULL, "
        "person TEXT DEFAULT '', "
        "status TEXT DEFAULT 'open', "
        "due_date TEXT, "
        "created_at TEXT DEFAULT (datetime('now')), "
        "closed_at TEXT"
        ");"
    )
    await _run(db_path, schema)


class FollowUpsTool:
    name = "follow_ups"
    description = "Track follow-up items — things waiting on others or needing check-back. Actions: list, create, close."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "create", "close"]},
            "id": {"type": "integer"},
            "item": {"type": "string", "description": "What to follow up on"},
            "person": {"type": "string", "description": "Who you're waiting on"},
            "due_date": {"type": "string", "description": "YYYY-MM-DD"},
            "status": {
                "type": "string",
                "enum": ["open", "closed", "all"],
                "description": "Filter for list action (default: open)",
            },
        },
        "required": ["action"],
    }

    def __init__(self, db_path: Path) -> None:
        self._db = db_path

    async def execute(self, **kwargs: Any) -> str:
        await _ensure_schema(self._db)
        action = kwargs["action"]

        if action == "list":
            return await self._list(**kwargs)
        if action == "create":
            return await self._create(**kwargs)
        if action == "close":
            return await self._close(**kwargs)
        return f"Unknown action: {action}"

    async def _list(self, **kwargs: Any) -> str:
        status_filter = kwargs.get("status", "open")
        if status_filter not in _VALID_STATUSES:
            return f"[ERROR] Invalid status '{_escape_sql_string(str(status_filter))}'. Valid: open, closed, all."

        if status_filter == "all":
            sql = "SELECT id, item, person, status, due_date, created_at, closed_at FROM follow_ups ORDER BY created_at DESC;"
        else:
            sql = (
                f"SELECT id, item, person, status, due_date, created_at, closed_at "
                f"FROM follow_ups WHERE status = '{status_filter}' "  # nosec B608 — whitelist-validated
                f"ORDER BY created_at DESC;"
            )
        rows = await _run(self._db, sql, json_mode=True)
        return rows or "[]"

    async def _create(self, **kwargs: Any) -> str:
        item = _escape_sql_string(kwargs.get("item", ""))
        person = _escape_sql_string(kwargs.get("person", ""))
        due = kwargs.get("due_date", "")
        if due:
            due_escaped = _escape_sql_string(due)
            due_val = f"'{due_escaped}'"
        else:
            due_val = "NULL"
        await _run(
            self._db,
            f"INSERT INTO follow_ups (item, person, due_date) "  # nosec B608 — escaped
            f"VALUES ('{item}', '{person}', {due_val});",
        )
        return f"Follow-up created: {kwargs.get('item', '')}"

    async def _close(self, **kwargs: Any) -> str:
        raw_id = kwargs.get("id")
        followup_id = _validate_int_id(raw_id)
        if followup_id is None:
            return f"[ERROR] Invalid follow-up id '{raw_id}'. Must be an integer."
        await _run(
            self._db,
            f"UPDATE follow_ups SET status = 'closed', "  # nosec B608 — validated int
            f"closed_at = datetime('now') WHERE id = {followup_id};",
        )
        return f"Follow-up {followup_id} closed."
