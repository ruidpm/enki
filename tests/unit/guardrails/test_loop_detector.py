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


def test_old_session_data_purged_on_set_session() -> None:
    """M-02: Switching sessions must purge old session data to prevent memory leak."""
    h = LoopDetectorHook(threshold=3)
    h.set_session("old-session-1")
    # Simulate some activity on old session
    h._counts["old-session-1"][("web_search", "abc")] = 2
    h.set_session("old-session-2")
    h._counts["old-session-2"][("tasks", "def")] = 1

    # Now switch to a new session
    h.set_session("new-session")

    # Old sessions should be purged
    assert "old-session-1" not in h._counts
    assert "old-session-2" not in h._counts


def test_current_session_preserved_on_set_session() -> None:
    """set_session must not break the new session's data."""
    h = LoopDetectorHook(threshold=3)
    h.set_session("session-a")
    h._counts["session-a"][("web_search", "abc")] = 1

    # Switch to new session — old is purged, new works fine
    h.set_session("session-b")
    assert h._current_session == "session-b"
    # New session should have an empty counts dict (or be creatable)
    assert len(h._counts.get("session-b", {})) == 0
