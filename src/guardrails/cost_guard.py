"""Cost guard — token budgets, dollar caps, autonomous turn limits."""
from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger()


class CostGuardHook:
    name = "cost_guard"

    def __init__(
        self,
        max_tokens_per_session: int,
        max_daily_cost_usd: float,
        max_monthly_cost_usd: float,
        max_llm_calls_per_session: int,
        max_autonomous_turns: int,
    ) -> None:
        self._max_tokens = max_tokens_per_session
        self._max_daily = max_daily_cost_usd
        self._max_monthly = max_monthly_cost_usd
        self._max_llm_calls = max_llm_calls_per_session
        self._max_autonomous_turns = max_autonomous_turns

        self._session_tokens = 0
        self._session_llm_calls = 0
        self._autonomous_turns = 0
        self._daily_cost_usd = 0.0
        self._monthly_cost_usd = 0.0

    def record_llm_call(
        self, input_tokens: int, output_tokens: int, cost_usd: float
    ) -> None:
        self._session_tokens += input_tokens + output_tokens
        self._session_llm_calls += 1
        self._daily_cost_usd += cost_usd
        self._monthly_cost_usd += cost_usd
        usage_pct = self._session_tokens / self._max_tokens
        if usage_pct >= 0.8:
            log.warning(
                "cost_guard_high_usage",
                pct=round(usage_pct * 100),
                tokens_used=self._session_tokens,
                tokens_max=self._max_tokens,
            )

    def record_autonomous_turn(self) -> None:
        self._autonomous_turns += 1

    def on_user_message(self) -> None:
        self._autonomous_turns = 0

    @property
    def daily_cost_usd(self) -> float:
        return self._daily_cost_usd

    @property
    def monthly_cost_usd(self) -> float:
        return self._monthly_cost_usd

    @property
    def session_tokens(self) -> int:
        return self._session_tokens

    def reset_session(self) -> None:
        """Reset per-session counters — called when starting a new conversation."""
        self._session_tokens = 0
        self._session_llm_calls = 0
        self._autonomous_turns = 0

    async def check(
        self, tool_name: str, params: dict[str, Any]
    ) -> tuple[bool, str | None]:
        if self._session_tokens >= self._max_tokens:
            return False, (
                f"Session token budget exhausted "
                f"({self._session_tokens}/{self._max_tokens})"
            )
        if self._session_llm_calls >= self._max_llm_calls:
            return False, (
                f"Session LLM call limit reached "
                f"({self._session_llm_calls}/{self._max_llm_calls})"
            )
        if self._daily_cost_usd >= self._max_daily:
            return False, (
                f"Daily cost limit reached "
                f"(${self._daily_cost_usd:.2f} / ${self._max_daily:.2f})"
            )
        if self._monthly_cost_usd >= self._max_monthly:
            return False, (
                f"Monthly cost limit reached "
                f"(${self._monthly_cost_usd:.2f} / ${self._max_monthly:.2f})"
            )
        if self._autonomous_turns >= self._max_autonomous_turns:
            return False, (
                f"Autonomous turn limit reached "
                f"({self._autonomous_turns}/{self._max_autonomous_turns}) "
                "— waiting for user input"
            )
        return True, None
