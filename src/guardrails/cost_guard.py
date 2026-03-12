"""Cost guard — token budgets, dollar caps, autonomous turn limits."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Protocol

import structlog

log = structlog.get_logger()


class _AlertNotifier(Protocol):
    async def send(self, message: str) -> None: ...


class CostGuardHook:
    name = "cost_guard"

    def __init__(
        self,
        max_tokens_per_session: int,
        max_daily_cost_usd: float,
        max_monthly_cost_usd: float,
        max_llm_calls_per_session: int,
        max_autonomous_turns: int,
        notifier: _AlertNotifier | None = None,
    ) -> None:
        self._max_tokens = max_tokens_per_session
        self._max_daily = max_daily_cost_usd
        self._max_monthly = max_monthly_cost_usd
        self._max_llm_calls = max_llm_calls_per_session
        self._max_autonomous_turns = max_autonomous_turns
        self._notifier = notifier

        self._session_tokens = 0
        self._session_llm_calls = 0
        self._autonomous_turns = 0
        self._daily_cost_usd = 0.0
        self._monthly_cost_usd = 0.0

        # Track which alert thresholds have already fired (no duplicates)
        self._fired_alerts: set[str] = set()

    def record_llm_call(self, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        self._session_tokens += input_tokens + output_tokens
        self._session_llm_calls += 1
        self._daily_cost_usd += cost_usd
        self._monthly_cost_usd += cost_usd

        # Check thresholds and fire alerts
        self._check_alerts()

    def _check_alerts(self) -> None:
        """Check budget thresholds and schedule notifications for any newly crossed."""
        alerts: list[str] = []

        # Session token usage
        token_pct = self._session_tokens / self._max_tokens
        if token_pct >= 0.9 and "tokens_90" not in self._fired_alerts:
            self._fired_alerts.add("tokens_90")
            alerts.append(f"Session token usage at 90% ({self._session_tokens:,}/{self._max_tokens:,})")
        elif token_pct >= 0.8 and "tokens_80" not in self._fired_alerts:
            self._fired_alerts.add("tokens_80")
            log.warning(
                "cost_guard_high_usage",
                pct=round(token_pct * 100),
                tokens_used=self._session_tokens,
                tokens_max=self._max_tokens,
            )
            alerts.append(f"Session token usage at 80% ({self._session_tokens:,}/{self._max_tokens:,})")

        # Daily cost
        daily_pct = self._daily_cost_usd / self._max_daily
        if daily_pct >= 0.9 and "daily_90" not in self._fired_alerts:
            self._fired_alerts.add("daily_90")
            alerts.append(f"Daily cost at 90% (${self._daily_cost_usd:.2f}/${self._max_daily:.2f})")
        elif daily_pct >= 0.75 and "daily_75" not in self._fired_alerts:
            self._fired_alerts.add("daily_75")
            alerts.append(f"Daily cost at 75% (${self._daily_cost_usd:.2f}/${self._max_daily:.2f})")

        # Monthly cost
        monthly_pct = self._monthly_cost_usd / self._max_monthly
        if monthly_pct >= 0.9 and "monthly_90" not in self._fired_alerts:
            self._fired_alerts.add("monthly_90")
            alerts.append(f"Monthly cost at 90% (${self._monthly_cost_usd:.2f}/${self._max_monthly:.2f})")
        elif monthly_pct >= 0.75 and "monthly_75" not in self._fired_alerts:
            self._fired_alerts.add("monthly_75")
            alerts.append(f"Monthly cost at 75% (${self._monthly_cost_usd:.2f}/${self._max_monthly:.2f})")

        if alerts and self._notifier is not None:
            msg = "Cost alert:\n" + "\n".join(f"- {a}" for a in alerts)
            with contextlib.suppress(RuntimeError):
                asyncio.get_event_loop().create_task(self._send_alert(msg))

    async def _send_alert(self, msg: str) -> None:
        """Fire-and-forget alert to notifier."""
        assert self._notifier is not None
        try:
            await self._notifier.send(msg)
        except Exception as exc:
            log.warning("cost_alert_send_failed", error=str(exc))

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
        self._fired_alerts = {a for a in self._fired_alerts if not a.startswith("tokens_")}

    async def check(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str | None]:
        if self._session_tokens >= self._max_tokens:
            return False, (f"Session token budget exhausted ({self._session_tokens}/{self._max_tokens})")
        if self._session_llm_calls >= self._max_llm_calls:
            return False, (f"Session LLM call limit reached ({self._session_llm_calls}/{self._max_llm_calls})")
        if self._daily_cost_usd >= self._max_daily:
            return False, (f"Daily cost limit reached (${self._daily_cost_usd:.2f} / ${self._max_daily:.2f})")
        if self._monthly_cost_usd >= self._max_monthly:
            return False, (f"Monthly cost limit reached (${self._monthly_cost_usd:.2f} / ${self._max_monthly:.2f})")
        if self._autonomous_turns >= self._max_autonomous_turns:
            return False, (
                f"Autonomous turn limit reached ({self._autonomous_turns}/{self._max_autonomous_turns}) — waiting for user input"
            )
        return True, None
