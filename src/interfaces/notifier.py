"""Canonical Notifier protocol — single source of truth for all notification contracts.

Every component that needs to send messages to the user or request confirmation
should import from here. Do NOT define ad-hoc Notifier protocols elsewhere.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    """Minimal notification contract used by most tools."""

    async def send(self, message: str) -> None: ...

    async def ask_confirm(self, tool_name: str, params: dict[str, Any]) -> bool: ...
