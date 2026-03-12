"""Unit tests for ConfirmationGateHook."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.guardrails.confirmation_gate import ConfirmationGateHook


def _make_notifier(confirm_result: bool = True) -> AsyncMock:
    """Build a mock Notifier whose ask_confirm returns *confirm_result*."""
    notifier = AsyncMock()
    notifier.ask_confirm = AsyncMock(return_value=confirm_result)
    return notifier


# ---------------------------------------------------------------------------
# 1. Tool in REQUIRES_CONFIRM → confirmed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_confirm_tool_approved() -> None:
    notifier = _make_notifier(confirm_result=True)
    hook = ConfirmationGateHook(notifier)

    # "git_commit" is in REQUIRES_CONFIRM
    allowed, reason = await hook.check("git_commit", {"message": "wip"})

    assert allowed is True
    assert reason is None
    notifier.ask_confirm.assert_awaited_once_with("git_commit", {"message": "wip"})


# ---------------------------------------------------------------------------
# 2. Tool in REQUIRES_CONFIRM → denied
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_confirm_tool_denied() -> None:
    notifier = _make_notifier(confirm_result=False)
    hook = ConfirmationGateHook(notifier)

    allowed, reason = await hook.check("git_commit", {"message": "wip"})

    assert allowed is False
    assert reason is not None
    assert "declined" in reason.lower()


# ---------------------------------------------------------------------------
# 3. Tool NOT in REQUIRES_CONFIRM → no notification
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_confirm_tool_passes_without_notification() -> None:
    notifier = _make_notifier()
    hook = ConfirmationGateHook(notifier)

    # "web_search" is not in REQUIRES_CONFIRM
    allowed, reason = await hook.check("web_search", {"query": "hello"})

    assert allowed is True
    assert reason is None
    notifier.ask_confirm.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4. Notifier raises an exception
#    Current code: exception propagates (no try/except around ask_confirm).
#    This test documents that gap — an unhandled exception in the notifier
#    will crash the guardrail hook rather than gracefully returning (False, ...).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_notifier_exception_propagates() -> None:
    """Documents current behaviour: if notifier.ask_confirm raises, the
    exception propagates out of check().  This is a coverage gap —
    ideally the hook would catch and return (False, reason)."""
    notifier = _make_notifier()
    notifier.ask_confirm = AsyncMock(side_effect=RuntimeError("network down"))
    hook = ConfirmationGateHook(notifier)

    with pytest.raises(RuntimeError, match="network down"):
        await hook.check("git_commit", {"message": "wip"})
