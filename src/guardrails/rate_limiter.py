"""Rate limiter — max N tool calls per agent turn."""
from __future__ import annotations

from typing import Any


class RateLimiterHook:
    name = "rate_limiter"

    def __init__(self, max_per_turn: int = 10) -> None:
        self._max = max_per_turn
        self._count = 0

    def reset(self) -> None:
        """Call at the start of each user turn."""
        self._count = 0

    async def check(
        self, tool_name: str, params: dict[str, Any]
    ) -> tuple[bool, str | None]:
        if self._count >= self._max:
            return False, (
                f"Rate limit: {self._count} tool calls this turn (max {self._max})"
            )
        self._count += 1
        return True, None
