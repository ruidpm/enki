"""System health check tool — diagnostics for databases, API, scheduler, disk."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

_EXPECTED_DBS = [
    "audit.db",
    "memory.db",
    "tasks.db",
    "teams.db",
    "workspaces.db",
    "pipelines.db",
    "schedule.db",
]


def _human_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} TB"


class HealthCheckTool:
    name = "health_check"
    description = (
        "Run a system health check: database integrity, disk usage, API connectivity, and scheduler status. No parameters needed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }

    def __init__(
        self,
        data_dir: Path,
        api_key: str | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._api_key = api_key
        self._scheduler_job_count: int | None = None
        self._scheduler_running: bool | None = None

    def set_scheduler_info(self, job_count: int, running: bool) -> None:
        """Inject scheduler status (called at wiring time or runtime)."""
        self._scheduler_job_count = job_count
        self._scheduler_running = running

    async def execute(self, **kwargs: Any) -> str:
        sections: list[str] = []

        sections.append(self._check_databases())
        sections.append(self._check_disk_usage())
        sections.append(await self._check_api())
        sections.append(self._check_scheduler())

        return "\n\n".join(s for s in sections if s)

    def _check_databases(self) -> str:
        """Check existence and integrity of all expected SQLite databases."""
        lines: list[str] = ["DATABASES"]
        for db_name in _EXPECTED_DBS:
            db_path = self._data_dir / db_name
            if not db_path.exists():
                lines.append(f"  {db_name}: missing / not found")
                continue

            size = _human_size(db_path.stat().st_size)
            integrity = self._integrity_check(db_path)
            status = "ok" if integrity == "ok" else f"FAIL ({integrity})"
            lines.append(f"  {db_name}: {size}, integrity: {status}")

        return "\n".join(lines)

    @staticmethod
    def _integrity_check(db_path: Path) -> str:
        """Run PRAGMA integrity_check on a SQLite database."""
        try:
            conn = sqlite3.connect(str(db_path))
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            return str(result[0]) if result else "unknown"
        except Exception as exc:
            return f"error: {exc}"

    def _check_disk_usage(self) -> str:
        """Report total size of the data directory."""
        if not self._data_dir.exists():
            return "DISK\n  data directory does not exist"

        total = sum(f.stat().st_size for f in self._data_dir.rglob("*") if f.is_file())
        return f"DISK\n  total data size: {_human_size(total)}"

    async def _check_api(self) -> str:
        """Check Anthropic API connectivity with a minimal request."""
        if not self._api_key:
            return "API\n  no API key configured, skipping"

        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=self._api_key)
            await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            return "API\n  Anthropic API: ok"
        except Exception as exc:
            return f"API\n  Anthropic API: error ({exc})"

    def _check_scheduler(self) -> str:
        """Report scheduler status if available."""
        if self._scheduler_job_count is None:
            return "SCHEDULER\n  not wired"

        status = "running" if self._scheduler_running else "stopped"
        return f"SCHEDULER\n  status: {status}, jobs: {self._scheduler_job_count}"
