"""Integration test — full GuardrailChain."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.guardrails import GuardrailChain
from src.guardrails.allowlist import AllowlistHook
from src.guardrails.confirmation_gate import ConfirmationGateHook
from src.guardrails.loop_detector import LoopDetectorHook
from src.guardrails.rate_limiter import RateLimiterHook


@pytest.fixture
def chain() -> GuardrailChain:
    registry: dict[str, Any] = {"web_search": object(), "tasks": object()}
    loop = LoopDetectorHook(threshold=3)
    loop.set_session("s1")
    return GuardrailChain(
        [
            AllowlistHook(registry),
            RateLimiterHook(max_per_turn=3),
            loop,
        ]
    )


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


# ---------------------------------------------------------------------------
# Confirmation gate — manage_workspace must be confirmed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manage_workspace_triggers_confirmation_gate_denied() -> None:
    """manage_workspace is in REQUIRES_CONFIRM — denied confirmation blocks execution."""
    notifier = AsyncMock()
    notifier.ask_confirm = AsyncMock(return_value=False)

    registry: dict[str, Any] = {"manage_workspace": object()}
    chain = GuardrailChain(
        [
            AllowlistHook(registry),
            ConfirmationGateHook(notifier),
        ]
    )

    allow, reason = await chain.run("manage_workspace", {"action": "set_trust", "workspace_id": "ws1", "trust_level": 4})
    assert allow is False
    assert "declined" in (reason or "").lower() or "confirm" in (reason or "").lower()
    notifier.ask_confirm.assert_awaited_once()


@pytest.mark.asyncio
async def test_manage_workspace_triggers_confirmation_gate_approved() -> None:
    """manage_workspace proceeds when user confirms."""
    notifier = AsyncMock()
    notifier.ask_confirm = AsyncMock(return_value=True)

    registry: dict[str, Any] = {"manage_workspace": object()}
    chain = GuardrailChain(
        [
            AllowlistHook(registry),
            ConfirmationGateHook(notifier),
        ]
    )

    allow, reason = await chain.run("manage_workspace", {"action": "clone", "workspace_id": "ws1"})
    assert allow is True
    assert reason is None
    notifier.ask_confirm.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_only_tools_skip_confirmation_gate() -> None:
    """git_status is not in REQUIRES_CONFIRM — no ask_confirm called."""
    notifier = AsyncMock()
    notifier.ask_confirm = AsyncMock(return_value=True)

    registry: dict[str, Any] = {"git_status": object()}
    chain = GuardrailChain(
        [
            AllowlistHook(registry),
            ConfirmationGateHook(notifier),
        ]
    )

    allow, reason = await chain.run("git_status", {})
    assert allow is True
    notifier.ask_confirm.assert_not_awaited()
