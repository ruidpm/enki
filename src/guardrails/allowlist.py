"""Allowlist guardrail — only registered tools can execute."""

from __future__ import annotations

from typing import Any

# Core tool names that self-evolution cannot overwrite
IMMUTABLE_CORE: frozenset[str] = frozenset(
    {
        "propose_tool",
        "request_restart",
        "git_status",
        "git_diff",
        "git_commit",
        "git_push_branch",
        "create_pr",
        "spawn_agent",
    }
)


class AllowlistHook:
    name = "allowlist"

    def __init__(self, registry: dict[str, Any]) -> None:
        self._registry = registry

    async def check(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str | None]:
        if tool_name not in self._registry:
            return False, f"Tool '{tool_name}' is not registered"
        return True, None
