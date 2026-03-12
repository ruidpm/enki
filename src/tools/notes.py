"""Project notes tool — markdown files via CLI (cat, grep, tee)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_\-]+$")
_MAX_CONTENT_SIZE = 1_000_000  # 1 MB max per write/append
_MAX_FILE_SIZE = 2_000_000  # 2 MB max total file size


class NotesTool:
    name = "notes"
    description = (
        "Read and write project notes stored as markdown files. "
        "Actions: list (all projects), read (project note), write (overwrite), append."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "read", "write", "append"]},
            "project": {"type": "string", "description": "Project name (alphanumeric, hyphens, underscores)"},
            "content": {"type": "string"},
        },
        "required": ["action"],
    }

    def __init__(self, notes_dir: Path) -> None:
        self._dir = notes_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, project: str) -> Path:
        if not _SAFE_NAME.match(project):
            raise ValueError(f"Invalid project name: '{project}'")
        return self._dir / f"{project}.md"

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs["action"]

        if action == "list":
            files = sorted(self._dir.glob("*.md"))
            if not files:
                return "No project notes yet."
            return "\n".join(f.stem for f in files)

        project = kwargs.get("project", "")
        if not project:
            return "Missing 'project' parameter."
        path = self._safe_path(project)

        if action == "read":
            if not path.exists():
                return f"No notes for project '{project}'."
            return path.read_text()

        content = kwargs.get("content", "")

        if action == "write":
            if len(content) > _MAX_CONTENT_SIZE:
                return f"Content too large ({len(content):,} bytes). Max write size is {_MAX_CONTENT_SIZE:,} bytes."
            path.write_text(content)
            return f"Notes for '{project}' saved."

        if action == "append":
            if len(content) > _MAX_CONTENT_SIZE:
                return f"Content too large ({len(content):,} bytes). Max append size is {_MAX_CONTENT_SIZE:,} bytes."
            existing_size = path.stat().st_size if path.exists() else 0
            if existing_size + len(content) > _MAX_FILE_SIZE:
                return (
                    f"Append would exceed {_MAX_FILE_SIZE:,} byte file limit "
                    f"(current: {existing_size:,}, append: {len(content):,})."
                )
            with path.open("a") as f:
                f.write(("\n" if path.exists() else "") + content)
            return f"Appended to '{project}' notes."

        return f"Unknown action: {action}"
