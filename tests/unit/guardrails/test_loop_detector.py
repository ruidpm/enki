"""Tests for LoopDetectorHook."""
from __future__ import annotations

import pytest

from src.guardrails.loop_detector import LoopDetectorHook


@pytest.fixture
def hook() -> LoopDetectorHook:
    h = LoopDetectorHook(threshold=3)
    h.set_session("test-session")
    return h


@pytest.mark.asyncio
async def test_allows_first_two_calls(hook: LoopDetectorHook) -> None:
    params = {"query": "test"}
    for _ in range(2):
        allow, _ = await hook.check("web_search", params)
        assert allow is True


@pytest.mark.asyncio
async def test_blocks_on_threshold(hook: LoopDetectorHook) -> None:
    params = {"query": "test"}
    for _ in range(2):
        await hook.check("web_search", params)
    allow, reason = await hook.check("web_search", params)
    assert allow is False
    assert "loop" in (reason or "").lower()


@pytest.mark.asyncio
async def test_different_params_not_blocked(hook: LoopDetectorHook) -> None:
    for i in range(5):
        allow, _ = await hook.check("web_search", {"query": f"query{i}"})
        assert allow is True


@pytest.mark.asyncio
async def test_different_tools_not_blocked(hook: LoopDetectorHook) -> None:
    params = {"action": "list"}
    for tool in ["tasks", "notes", "web_search", "tasks", "notes"]:
        allow, _ = await hook.check(tool, params)
        assert allow is True


@pytest.mark.asyncio
async def test_resets_on_user_message(hook: LoopDetectorHook) -> None:
    params = {"query": "test"}
    for _ in range(2):
        await hook.check("web_search", params)
    hook.on_user_message()
    # Should be reset — 2 more calls should be fine
    for _ in range(2):
        allow, _ = await hook.check("web_search", params)
        assert allow is True
