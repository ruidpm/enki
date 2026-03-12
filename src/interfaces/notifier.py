"""Canonical Notifier protocol — single source of truth for all notification contracts.

Every component that needs to send messages to the user or request confirmation
should import from here. Do NOT define ad-hoc Notifier protocols elsewhere.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    """Full notification contract — covers all tool and guardrail needs."""

    async def send(self, message: str) -> None: ...

    async def ask_confirm(self, tool_name: str, params: dict[str, Any]) -> bool: ...

    async def ask_single_confirm(self, reason: str, changes_summary: str) -> bool: ...

    async def ask_double_confirm(self, reason: str, changes_summary: str) -> bool: ...

    async def ask_free_text(self, prompt: str, timeout_s: int = 300) -> str | None: ...

    async def send_diff(self, tool_name: str, description: str, code: str, code_hash: str) -> None: ...

    async def wait_for_approval(self, tool_name: str) -> bool: ...
