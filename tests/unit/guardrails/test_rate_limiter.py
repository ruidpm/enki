"""Tests for RateLimiterHook (H-05 / M-03)."""

from __future__ import annotations

import pytest

from src.guardrails.rate_limiter import RateLimiterHook


@pytest.fixture
def limiter() -> RateLimiterHook:
    return RateLimiterHook(max_per_turn=10)


@pytest.mark.asyncio
async def test_allows_up_to_max_calls(limiter: RateLimiterHook) -> None:
    """Exactly max_per_turn calls should be allowed."""
    for i in range(10):
        allow, reason = await limiter.check("tool", {})
        assert allow is True, f"Call {i + 1} should be allowed"


@pytest.mark.asyncio
async def test_blocks_call_after_max(limiter: RateLimiterHook) -> None:
    """The (max+1)th call should be blocked."""
    for _ in range(10):
        await limiter.check("tool", {})

    allow, reason = await limiter.check("tool", {})
    assert allow is False
    assert reason is not None
    assert "Rate limit" in reason


@pytest.mark.asyncio
async def test_reset_clears_counter(limiter: RateLimiterHook) -> None:
    """After reset, the counter should start fresh."""
    for _ in range(10):
        await limiter.check("tool", {})

    limiter.reset()

    allow, _ = await limiter.check("tool", {})
    assert allow is True


@pytest.mark.asyncio
async def test_single_call_allowed() -> None:
    """A limiter with max=1 should allow exactly 1 call."""
    limiter = RateLimiterHook(max_per_turn=1)

    allow, _ = await limiter.check("tool", {})
    assert allow is True

    allow, reason = await limiter.check("tool", {})
    assert allow is False


@pytest.mark.asyncio
async def test_no_off_by_one() -> None:
    """With max=10, call 10 allowed, call 11 blocked (not call 11 allowed, 12 blocked)."""
    limiter = RateLimiterHook(max_per_turn=10)

    results = []
    for _ in range(12):
        allow, _ = await limiter.check("tool", {})
        results.append(allow)

    # First 10 should be True, rest False
    assert results == [True] * 10 + [False] * 2
