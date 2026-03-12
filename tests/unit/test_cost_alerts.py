"""Tests for proactive cost alerts — user notified at 75%/90% of budget thresholds."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.guardrails.cost_guard import CostGuardHook


class TestProactiveCostAlerts:
    """CostGuardHook should notify user at 75% and 90% of budget thresholds."""

    @pytest.fixture
    def notifier(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def guard(self, notifier: AsyncMock) -> CostGuardHook:
        return CostGuardHook(
            max_tokens_per_session=1000,
            max_daily_cost_usd=10.0,
            max_monthly_cost_usd=100.0,
            max_llm_calls_per_session=100,
            max_autonomous_turns=10,
            notifier=notifier,
        )

    @pytest.mark.asyncio
    async def test_no_alert_below_75_pct(self, guard: CostGuardHook, notifier: AsyncMock) -> None:
        guard.record_llm_call(100, 50, 5.0)  # 50% of daily
        await asyncio.sleep(0)  # let fire-and-forget tasks run
        notifier.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_at_75_pct_daily(self, guard: CostGuardHook, notifier: AsyncMock) -> None:
        guard.record_llm_call(100, 50, 7.5)  # 75% of $10
        await asyncio.sleep(0)
        notifier.send.assert_called_once()
        msg = notifier.send.call_args[0][0]
        assert "75%" in msg
        assert "daily" in msg.lower() or "Daily" in msg

    @pytest.mark.asyncio
    async def test_alert_at_90_pct_daily(self, guard: CostGuardHook, notifier: AsyncMock) -> None:
        guard.record_llm_call(100, 50, 7.5)  # 75%
        await asyncio.sleep(0)
        notifier.send.reset_mock()
        guard.record_llm_call(100, 50, 1.5)  # now 90%
        await asyncio.sleep(0)
        notifier.send.assert_called_once()
        msg = notifier.send.call_args[0][0]
        assert "90%" in msg

    @pytest.mark.asyncio
    async def test_alert_at_75_pct_monthly(self, guard: CostGuardHook, notifier: AsyncMock) -> None:
        guard.record_llm_call(100, 50, 75.0)  # 75% of $100
        await asyncio.sleep(0)
        notifier.send.assert_called()
        msgs = [c[0][0] for c in notifier.send.call_args_list]
        assert any("monthly" in m.lower() or "Monthly" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_no_duplicate_alerts(self, guard: CostGuardHook, notifier: AsyncMock) -> None:
        """Each threshold should only fire once."""
        guard.record_llm_call(100, 50, 7.5)  # 75% daily
        await asyncio.sleep(0)
        count_after_first = notifier.send.call_count
        guard.record_llm_call(10, 5, 0.01)  # still ~75%, no new alert
        await asyncio.sleep(0)
        assert notifier.send.call_count == count_after_first

    @pytest.mark.asyncio
    async def test_session_token_alert_at_80_pct(self, guard: CostGuardHook, notifier: AsyncMock) -> None:
        guard.record_llm_call(800, 0, 0.01)  # 80% of 1000 tokens
        await asyncio.sleep(0)
        notifier.send.assert_called()
        msgs = [c[0][0] for c in notifier.send.call_args_list]
        assert any("token" in m.lower() or "session" in m.lower() or "Session" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_no_alert_without_notifier(self) -> None:
        """Guard without notifier should not crash on alerts."""
        guard = CostGuardHook(
            max_tokens_per_session=1000,
            max_daily_cost_usd=10.0,
            max_monthly_cost_usd=100.0,
            max_llm_calls_per_session=100,
            max_autonomous_turns=10,
        )
        # Should not raise
        guard.record_llm_call(800, 0, 9.5)
        await asyncio.sleep(0)
