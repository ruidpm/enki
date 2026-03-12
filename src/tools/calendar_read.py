"""Calendar read tool — via gcalcli CLI. Read-only, no write methods exist."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import structlog

log = structlog.get_logger()

_GCALCLI = "gcalcli"  # permitted CLI binary


class CalendarReadTool:
    name = "calendar_read"
    description = (
        "Read upcoming calendar events. Read-only — cannot create or modify events. "
        "Requires gcalcli installed and configured with Google account."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "default": 7,
                "minimum": 1,
                "maximum": 30,
                "description": "Number of days to look ahead",
            }
        },
    }

    async def execute(self, **kwargs: Any) -> str:
        days = min(int(kwargs.get("days", 7)), 30)
        start = date.today().strftime("%Y-%m-%d")
        end = (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")
        proc = await asyncio.create_subprocess_exec(
            _GCALCLI,
            "agenda",
            "--nocolor",
            start,
            end,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"gcalcli error: {stderr.decode().strip()}"
        output = stdout.decode().strip()
        return output or "No events found."
