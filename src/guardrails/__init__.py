"""Guardrail hook chain — deterministic, fail-fast."""

from __future__ import annotations

from typing import Any, Protocol

import structlog

log = structlog.get_logger()


class GuardrailHook(Protocol):
    name: str

    async def check(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str | None]:
        """Returns (allow, reason). allow=False means BLOCK."""
        ...


class GuardrailChain:
    """Runs hooks in order. First BLOCK stops the chain."""

    def __init__(self, hooks: list[GuardrailHook]) -> None:
        self._hooks = hooks

    async def run(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str | None]:
        """Run all hooks sequentially. Returns (allow, reason)."""
        for hook in self._hooks:
            allow, reason = await hook.check(tool_name, params)
            if not allow:
                log.warning(
                    "guardrail_blocked",
                    hook=hook.name,
                    tool=tool_name,
                    reason=reason,
                )
                return False, f"[{hook.name}] {reason}"
        return True, None
