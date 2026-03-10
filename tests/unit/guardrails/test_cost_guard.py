"""Tests for CostGuardHook."""
from __future__ import annotations

import pytest

from src.guardrails.cost_guard import CostGuardHook


@pytest.fixture
def hook() -> CostGuardHook:
    return CostGuardHook(
        max_tokens_per_session=1000,
        max_daily_cost_usd=1.0,
        max_monthly_cost_usd=10.0,
        max_llm_calls_per_session=5,
        max_autonomous_turns=3,
    )


@pytest.mark.asyncio
async def test_allows_under_budget(hook: CostGuardHook) -> None:
    allow, _ = await hook.check("web_search", {})
    assert allow is True


@pytest.mark.asyncio
async def test_blocks_on_token_exhaustion(hook: CostGuardHook) -> None:
    hook.record_llm_call(500, 501, 0.001)  # 1001 tokens > 1000 max
    allow, reason = await hook.check("web_search", {})
    assert allow is False
    assert "token" in (reason or "").lower()


@pytest.mark.asyncio
async def test_blocks_on_llm_call_limit(hook: CostGuardHook) -> None:
    for _ in range(5):
        hook.record_llm_call(10, 10, 0.0001)
    allow, reason = await hook.check("web_search", {})
    assert allow is False
    assert "LLM call" in (reason or "")


@pytest.mark.asyncio
async def test_blocks_on_daily_cost(hook: CostGuardHook) -> None:
    hook.record_llm_call(1, 1, 1.01)  # over $1.00 daily limit
    allow, reason = await hook.check("web_search", {})
    assert allow is False
    assert "Daily" in (reason or "")


@pytest.mark.asyncio
async def test_blocks_autonomous_turns(hook: CostGuardHook) -> None:
    for _ in range(3):
        hook.record_autonomous_turn()
    allow, reason = await hook.check("web_search", {})
    assert allow is False
    assert "Autonomous" in (reason or "")


@pytest.mark.asyncio
async def test_autonomous_turns_reset_on_user_message(hook: CostGuardHook) -> None:
    for _ in range(3):
        hook.record_autonomous_turn()
    hook.on_user_message()
    allow, _ = await hook.check("web_search", {})
    assert allow is True
