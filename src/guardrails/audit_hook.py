"""Audit hook — records every tool call outcome (always runs, never blocks)."""
from __future__ import annotations

from typing import Any, Protocol


class AuditWriter(Protocol):
    async def log_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any],
        allowed: bool,
        block_reason: str | None,
        session_id: str,
    ) -> None: ...


class AuditHook:
    """Sits at the end of the chain. Never blocks — only logs."""

    name = "audit"

    def __init__(self, writer: AuditWriter, session_id: str) -> None:
        self._writer = writer
        self._session_id = session_id

    async def check(
        self, tool_name: str, params: dict[str, Any]
    ) -> tuple[bool, str | None]:
        # Never blocks — logging happens via record() after chain decision
        return True, None

    async def record(
        self,
        tool_name: str,
        params: dict[str, Any],
        allowed: bool,
        reason: str | None,
    ) -> None:
        await self._writer.log_tool_call(
            tool_name=tool_name,
            params=params,
            allowed=allowed,
            block_reason=reason,
            session_id=self._session_id,
        )
