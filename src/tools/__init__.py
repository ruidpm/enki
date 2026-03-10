"""Tool registry — allowlist-enforced dispatch."""
from __future__ import annotations

from typing import Any, Protocol

from ..guardrails.allowlist import IMMUTABLE_CORE


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]

    async def execute(self, **kwargs: Any) -> str: ...


# Live registry — populated by register() calls at import time
registry: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    # Block self-evolution from overwriting already-registered immutable tools
    if tool.name in IMMUTABLE_CORE and tool.name in registry:
        raise ValueError(f"Cannot register over immutable core tool: '{tool.name}'")
    registry[tool.name] = tool
