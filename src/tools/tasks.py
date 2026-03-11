"""Task management tool — CRUD via sqlite3 CLI."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

# Permitted CLI binary — enforced by code_scanner allowlist
_SQLITE = "sqlite3"

# Whitelist of valid status values
_VALID_STATUSES: frozenset[str] = frozenset({"open", "done"})

# Pattern for valid due_date: YYYY-MM-DD only
_DUE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _escape_sql_string(value: str) -> str:
    """Escape a string for safe inclusion in single-quoted SQL literal.

    Doubles single quotes (the only escape needed for SQLite string literals).
    """
    return value.replace("'", "''")


def _validate_int_id(value: Any) -> int | None:
    """Validate that value is (or can be safely parsed as) an integer.

    Returns the integer on success, None on failure.
    """
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
        "CREATE TABLE IF NOT EXISTS tasks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "title TEXT NOT NULL, "
        "notes TEXT DEFAULT '', "
        "status TEXT DEFAULT 'open', "
        "due_date TEXT, "
        "created_at TEXT DEFAULT (datetime('now')), "
        "updated_at TEXT DEFAULT (datetime('now'))"
        ");"
    )
    await _run(db_path, schema)


class TasksTool:
    name = "tasks"
    description = (
        "Manage personal tasks. Actions: list, create, update, delete. "
        "Each task has: id, title, notes, status (open/done), due_date (YYYY-MM-DD)."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "create", "update", "delete"]},
            "id": {"type": "integer"},
            "title": {"type": "string"},
            "notes": {"type": "string"},
            "status": {"type": "string", "enum": ["open", "done"]},
            "due_date": {"type": "string", "description": "YYYY-MM-DD"},
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
        if action == "update":
            return await self._update(**kwargs)
        if action == "delete":
            return await self._delete(**kwargs)
        return f"Unknown action: {action}"

    async def _list(self, **kwargs: Any) -> str:
        status_filter = kwargs.get("status", "open")
        if status_filter not in _VALID_STATUSES:
            return f"[ERROR] Invalid status '{_escape_sql_string(str(status_filter))}'. Valid: open, done."
        rows = await _run(
            self._db,
            f"SELECT id, title, notes, status, due_date, created_at "
            f"FROM tasks WHERE status = '{status_filter}' ORDER BY due_date, id;",
            json_mode=True,
        )
        return rows or "[]"

    async def _create(self, **kwargs: Any) -> str:
        title = _escape_sql_string(kwargs.get("title", ""))
        notes = _escape_sql_string(kwargs.get("notes", ""))
        due = kwargs.get("due_date", "")
        if due:
            due_escaped = _escape_sql_string(due)
            due_val = f"'{due_escaped}'"
        else:
            due_val = "NULL"
        await _run(
            self._db,
            f"INSERT INTO tasks (title, notes, due_date) VALUES ('{title}', '{notes}', {due_val});",
        )
        return f"Task created: {kwargs.get('title', '')}"

    async def _update(self, **kwargs: Any) -> str:
        raw_id = kwargs.get("id")
        task_id = _validate_int_id(raw_id)
        if task_id is None:
            return f"[ERROR] Invalid task id '{raw_id}'. Must be an integer."

        fields: list[str] = []
        if "title" in kwargs:
            fields.append(f"title = '{_escape_sql_string(str(kwargs['title']))}'")
        if "notes" in kwargs:
            fields.append(f"notes = '{_escape_sql_string(str(kwargs['notes']))}'")
        if "status" in kwargs:
            status = kwargs["status"]
            if status not in _VALID_STATUSES:
                return f"[ERROR] Invalid status '{_escape_sql_string(str(status))}'. Valid: open, done."
            fields.append(f"status = '{status}'")
        if "due_date" in kwargs:
            due = _escape_sql_string(str(kwargs["due_date"]))
            fields.append(f"due_date = '{due}'")
        if not fields:
            return "No fields to update."
        fields.append("updated_at = datetime('now')")
        await _run(
            self._db,
            f"UPDATE tasks SET {', '.join(fields)} WHERE id = {task_id};",
        )
        return f"Task {task_id} updated."

    async def _delete(self, **kwargs: Any) -> str:
        raw_id = kwargs.get("id")
        task_id = _validate_int_id(raw_id)
        if task_id is None:
            return f"[ERROR] Invalid task id '{raw_id}'. Must be an integer."
        await _run(self._db, f"DELETE FROM tasks WHERE id = {task_id};")
        return f"Task {task_id} deleted."
