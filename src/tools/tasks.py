"""Task management tool — CRUD via sqlite3 CLI."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

# Permitted CLI binary — enforced by code_scanner allowlist
_SQLITE = "sqlite3"


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
            status_filter = kwargs.get("status", "open")
            rows = await _run(
                self._db,
                f"SELECT id, title, notes, status, due_date, created_at "
                f"FROM tasks WHERE status = '{status_filter}' ORDER BY due_date, id;",
                json_mode=True,
            )
            return rows or "[]"

        if action == "create":
            title = kwargs.get("title", "").replace("'", "''")
            notes = kwargs.get("notes", "").replace("'", "''")
            due = kwargs.get("due_date", "")
            due_val = f"'{due}'" if due else "NULL"
            await _run(
                self._db,
                f"INSERT INTO tasks (title, notes, due_date) VALUES ('{title}', '{notes}', {due_val});",
            )
            return f"Task created: {title}"

        if action == "update":
            task_id = kwargs["id"]
            fields = []
            if "title" in kwargs:
                fields.append(f"title = '{str(kwargs['title']).replace(chr(39), chr(39)*2)}'")
            if "notes" in kwargs:
                fields.append(f"notes = '{str(kwargs['notes']).replace(chr(39), chr(39)*2)}'")
            if "status" in kwargs:
                fields.append(f"status = '{kwargs['status']}'")
            if "due_date" in kwargs:
                fields.append(f"due_date = '{kwargs['due_date']}'")
            if not fields:
                return "No fields to update."
            fields.append("updated_at = datetime('now')")
            await _run(
                self._db,
                f"UPDATE tasks SET {', '.join(fields)} WHERE id = {task_id};",
            )
            return f"Task {task_id} updated."

        if action == "delete":
            task_id = kwargs["id"]
            await _run(self._db, f"DELETE FROM tasks WHERE id = {task_id};")
            return f"Task {task_id} deleted."

        return f"Unknown action: {action}"
