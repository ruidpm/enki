"""Confirmation gate — write operations require explicit user Y/N."""
from __future__ import annotations

from typing import Any, Protocol

# Tools that must be confirmed before execution
REQUIRES_CONFIRM: frozenset[str] = frozenset({
    "create_task",
    "update_task",
    "delete_task",
    "git_commit",
    "git_push_branch",
    "create_pr",
    "request_restart",
    "propose_tool",
    "remove_tool",
    "manage_team",
    "manage_schedule",
    "manage_workspace",
})


class Notifier(Protocol):
    async def ask_confirm(self, tool_name: str, params: dict[str, Any]) -> bool:
        """Send Y/N prompt to user, return True if confirmed."""
        ...


class ConfirmationGateHook:
    name = "confirmation_gate"

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def check(
        self, tool_name: str, params: dict[str, Any]
    ) -> tuple[bool, str | None]:
        if tool_name not in REQUIRES_CONFIRM:
            return True, None
        confirmed = await self._notifier.ask_confirm(tool_name, params)
        if not confirmed:
            return False, "User declined confirmation"
        return True, None
