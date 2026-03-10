"""Integration test — full GuardrailChain."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.guardrails import GuardrailChain
from src.guardrails.allowlist import AllowlistHook
from src.guardrails.rate_limiter import RateLimiterHook
from src.guardrails.loop_detector import LoopDetectorHook


@pytest.fixture
def chain() -> GuardrailChain:
    registry: dict[str, Any] = {"web_search": object(), "tasks": object()}
    loop = LoopDetectorHook(threshold=3)
    loop.set_session("s1")
    return GuardrailChain([
        AllowlistHook(registry),
        RateLimiterHook(max_per_turn=3),
        loop,
    ])


@pytest.mark.asyncio
async def test_chain_allows_valid_call(chain: GuardrailChain) -> None:
    allow, reason = await chain.run("web_search", {"query": "test"})
    assert allow is True
    assert reason is None


@pytest.mark.asyncio
async def test_chain_blocks_unregistered(chain: GuardrailChain) -> None:
    allow, reason = await chain.run("evil_tool", {})
    assert allow is False
    assert "allowlist" in (reason or "").lower()


@pytest.mark.asyncio
async def test_chain_blocks_after_rate_limit(chain: GuardrailChain) -> None:
    for _ in range(3):
        await chain.run("web_search", {"query": "a"})
    allow, reason = await chain.run("tasks", {"action": "list"})
    assert allow is False
    assert "rate_limiter" in (reason or "").lower()


@pytest.mark.asyncio
async def test_chain_stops_at_first_block(chain: GuardrailChain) -> None:
    """Unregistered tool should block at allowlist, not proceed to rate limiter."""
    allow, reason = await chain.run("ghost", {})
    assert allow is False
    assert "[allowlist]" in (reason or "")
