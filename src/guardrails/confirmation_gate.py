"""Confirmation gate — write operations require explicit user Y/N."""

from __future__ import annotations

from typing import Any

from src.constants import REQUIRES_CONFIRM
from src.interfaces.notifier import Notifier


class ConfirmationGateHook:
    name = "confirmation_gate"

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def check(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str | None]:
        if tool_name not in REQUIRES_CONFIRM:
            return True, None
        confirmed = await self._notifier.ask_confirm(tool_name, params)
        if not confirmed:
            return False, "User declined confirmation"
        return True, None
