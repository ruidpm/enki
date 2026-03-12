"""Canonical Agent protocol — used by tools that need a back-reference to the agent.

Named AgentProtocol to avoid collision with src.agent.Agent (the concrete class).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentProtocol(Protocol):
    """Minimal contract for tools that call back into the agent."""

    async def run_turn(self, user_message: str | list[dict[str, Any]]) -> str: ...
