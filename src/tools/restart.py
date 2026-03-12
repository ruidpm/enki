"""Controlled self-restart tool.

SECURITY NOTE: This is the ONLY file in the codebase permitted to use subprocess.
The subprocess import is kept for the local (non-Docker) os.execv path.
code_scanner.py grants subprocess a path-based exception for this file only.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Protocol

import structlog

log = structlog.get_logger()

_COOLDOWN_SECONDS = 600  # 10-minute minimum between restarts


def is_running_in_docker() -> bool:
    """Detect whether we're inside a Docker container."""
    return Path("/.dockerenv").exists()


class RestartNotifier(Protocol):
    async def send(self, message: str) -> None: ...
    async def ask_double_confirm(self, reason: str, changes_summary: str) -> bool: ...


class RequestRestartTool:
    name = "request_restart"
    description = (
        "Request a controlled assistant restart to apply core code changes "
        "(agent.py, guardrails, main.py). NOT needed for new tools — those activate immediately. "
        "Requires double confirmation. 10-minute cooldown enforced."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Why a restart is needed"},
            "changes_summary": {"type": "string", "description": "What changed"},
        },
        "required": ["reason", "changes_summary"],
    }

    def __init__(self, notifier: RestartNotifier) -> None:
        self._notifier = notifier
        self._last_restart: float = 0.0  # instance-level only — no module global

    async def execute(self, **kwargs: Any) -> str:
        reason: str = kwargs["reason"]
        changes_summary: str = kwargs["changes_summary"]

        # Cooldown check
        elapsed = time.time() - self._last_restart
        if self._last_restart > 0 and elapsed < _COOLDOWN_SECONDS:
            remaining = int(_COOLDOWN_SECONDS - elapsed)
            return f"[BLOCKED] Restart cooldown active — {remaining}s remaining."

        # Double confirmation
        confirmed = await self._notifier.ask_double_confirm(reason, changes_summary)
        if not confirmed:
            return "Restart cancelled — user did not confirm."

        log.warning(
            "restart_confirmed",
            reason=reason,
            changes_summary=changes_summary,
        )

        await self._notifier.send("Restarting now — back in ~30 seconds.")
        self._last_restart = time.time()

        if is_running_in_docker():
            # Signal ourselves — Docker's restart: unless-stopped will bring us back up.
            # Cleaner than mounting the Docker socket; PTB handles SIGTERM gracefully.
            os.kill(os.getpid(), signal.SIGTERM)
        else:
            # Local process restart — replaces current process with a fresh one
            os.execv(sys.executable, [sys.executable] + sys.argv)

        return "Restart initiated."
