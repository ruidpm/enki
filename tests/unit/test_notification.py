"""Tests for SmartNotifier — notification intelligence with priority classification and quiet hours."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.interfaces.notifier import Notifier
from src.notification import Priority, SmartNotifier


def _make_inner() -> AsyncMock:
    """Create a mock that satisfies the Notifier protocol."""
    mock = AsyncMock(spec=Notifier)
    return mock


def _make_notifier(
    *,
    quiet_start: int = 22,
    quiet_end: int = 8,
    timezone: str = "UTC",
    now_hour: int | None = None,
) -> tuple[SmartNotifier, AsyncMock]:
    inner = _make_inner()
    sn = SmartNotifier(inner, quiet_start=quiet_start, quiet_end=quiet_end, timezone=timezone)
    if now_hour is not None:
        sn._now_hour = lambda: now_hour  # type: ignore[method-assign]
    return sn, inner


# --- Classification tests ---


class TestClassify:
    def test_error_is_urgent(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Something error happened") == Priority.URGENT

    def test_failed_is_urgent(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Task failed to complete") == Priority.URGENT

    def test_crashed_is_urgent(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Process crashed unexpectedly") == Priority.URGENT

    def test_confirm_is_urgent(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Please confirm this action") == Priority.URGENT

    def test_approve_is_urgent(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Waiting for you to approve") == Priority.URGENT

    def test_proceed_is_urgent(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Shall I proceed?") == Priority.URGENT

    def test_90_percent_budget_is_urgent(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Cost alert: 90% of daily budget used") == Priority.URGENT

    def test_budget_keyword_is_urgent(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Budget limit approaching") == Priority.URGENT

    def test_80_percent_cost_is_low(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Cost alert: 80% of monthly limit used") == Priority.LOW

    def test_fyi_is_low(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("FYI: backup completed") == Priority.LOW

    def test_info_prefix_is_low(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Info: scheduled job ran successfully") == Priority.LOW

    def test_default_is_normal(self) -> None:
        sn, _ = _make_notifier()
        assert sn._classify("Pipeline stage 2 complete, starting stage 3") == Priority.NORMAL


# --- Delivery tests ---


class TestDelivery:
    @pytest.mark.asyncio
    async def test_urgent_delivers_immediately(self) -> None:
        """Urgent messages are delivered even during quiet hours."""
        sn, inner = _make_notifier(now_hour=23)
        await sn.send("Something error occurred")
        inner.send.assert_awaited_once_with("Something error occurred")

    @pytest.mark.asyncio
    async def test_normal_delivers_outside_quiet_hours(self) -> None:
        """Normal messages are delivered immediately when not in quiet hours."""
        sn, inner = _make_notifier(now_hour=14)
        await sn.send("Pipeline complete")
        inner.send.assert_awaited_once_with("Pipeline complete")

    @pytest.mark.asyncio
    async def test_normal_queued_during_quiet_hours(self) -> None:
        """Normal messages are queued during quiet hours."""
        sn, inner = _make_notifier(now_hour=23)
        await sn.send("Pipeline complete")
        inner.send.assert_not_awaited()
        assert len(sn._queue) == 1
        assert sn._queue[0] == "Pipeline complete"

    @pytest.mark.asyncio
    async def test_urgent_delivers_during_quiet_hours(self) -> None:
        """Urgent messages bypass quiet hours."""
        sn, inner = _make_notifier(now_hour=2)
        await sn.send("Task failed with exit code 1")
        inner.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_low_queued_during_quiet_hours(self) -> None:
        """Low-priority messages are queued during quiet hours."""
        sn, inner = _make_notifier(now_hour=23)
        await sn.send("FYI: daily backup done")
        inner.send.assert_not_awaited()
        assert len(sn._queue) == 1

    @pytest.mark.asyncio
    async def test_low_delivers_outside_quiet_hours(self) -> None:
        """Low-priority messages deliver immediately outside quiet hours."""
        sn, inner = _make_notifier(now_hour=14)
        await sn.send("FYI: daily backup done")
        inner.send.assert_awaited_once()


# --- Flush tests ---


class TestFlush:
    @pytest.mark.asyncio
    async def test_flush_sends_batched(self) -> None:
        """Flushing sends all queued messages as one batched message."""
        sn, inner = _make_notifier(now_hour=23)
        await sn.send("Pipeline complete")
        await sn.send("Info: backup done")
        assert len(sn._queue) == 2

        # Now flush (simulate quiet hours ending)
        await sn.flush_queue()
        inner.send.assert_awaited_once()
        sent_msg: str = inner.send.call_args[0][0]
        assert "Pipeline complete" in sent_msg
        assert "backup done" in sent_msg
        assert "\n---\n" in sent_msg
        assert len(sn._queue) == 0

    @pytest.mark.asyncio
    async def test_flush_empty_queue_noop(self) -> None:
        """Flushing an empty queue does not send anything."""
        sn, inner = _make_notifier()
        await sn.flush_queue()
        inner.send.assert_not_awaited()


# --- Pass-through tests ---


class TestPassThrough:
    @pytest.mark.asyncio
    async def test_ask_confirm_always_passes_through(self) -> None:
        """Confirmation methods always delegate directly regardless of quiet hours."""
        sn, inner = _make_notifier(now_hour=3)
        inner.ask_confirm.return_value = True
        result = await sn.ask_confirm("some_tool", {"key": "val"})
        assert result is True
        inner.ask_confirm.assert_awaited_once_with("some_tool", {"key": "val"})

    @pytest.mark.asyncio
    async def test_ask_single_confirm_passes_through(self) -> None:
        sn, inner = _make_notifier(now_hour=3)
        inner.ask_single_confirm.return_value = False
        result = await sn.ask_single_confirm("reason", "summary")
        assert result is False
        inner.ask_single_confirm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ask_double_confirm_passes_through(self) -> None:
        sn, inner = _make_notifier(now_hour=3)
        inner.ask_double_confirm.return_value = True
        result = await sn.ask_double_confirm("reason", "summary")
        assert result is True
        inner.ask_double_confirm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ask_free_text_passes_through(self) -> None:
        sn, inner = _make_notifier(now_hour=3)
        inner.ask_free_text.return_value = "user typed this"
        result = await sn.ask_free_text("What do you think?", timeout_s=60)
        assert result == "user typed this"
        inner.ask_free_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ask_scope_approval_passes_through(self) -> None:
        sn, inner = _make_notifier(now_hour=3)
        inner.ask_scope_approval.return_value = "approve"
        result = await sn.ask_scope_approval("Scope check", timeout_s=120)
        assert result == "approve"
        inner.ask_scope_approval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_diff_passes_through(self) -> None:
        sn, inner = _make_notifier(now_hour=3)
        await sn.send_diff("tool", "desc", "code", "hash123")
        inner.send_diff.assert_awaited_once_with("tool", "desc", "code", "hash123")

    @pytest.mark.asyncio
    async def test_wait_for_approval_passes_through(self) -> None:
        sn, inner = _make_notifier(now_hour=3)
        inner.wait_for_approval.return_value = True
        result = await sn.wait_for_approval("tool")
        assert result is True
        inner.wait_for_approval.assert_awaited_once_with("tool")


# --- Protocol conformance ---


class TestProtocol:
    def test_isinstance_notifier(self) -> None:
        """SmartNotifier must pass isinstance(x, Notifier) check."""
        sn, _ = _make_notifier()
        assert isinstance(sn, Notifier)


# --- Quiet hours boundary tests ---


class TestQuietHours:
    def test_wrapping_quiet_hours(self) -> None:
        """Quiet hours that wrap midnight (22:00-08:00)."""
        sn, _ = _make_notifier(quiet_start=22, quiet_end=8)
        # During quiet hours
        sn._now_hour = lambda: 23  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is True
        sn._now_hour = lambda: 0  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is True
        sn._now_hour = lambda: 7  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is True
        # Outside quiet hours
        sn._now_hour = lambda: 8  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is False
        sn._now_hour = lambda: 14  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is False
        sn._now_hour = lambda: 21  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is False

    def test_non_wrapping_quiet_hours(self) -> None:
        """Quiet hours that don't wrap midnight (e.g., 01:00-06:00)."""
        sn, _ = _make_notifier(quiet_start=1, quiet_end=6)
        sn._now_hour = lambda: 3  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is True
        sn._now_hour = lambda: 0  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is False
        sn._now_hour = lambda: 7  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is False

    def test_same_start_end_means_no_quiet_hours(self) -> None:
        """If start == end, quiet hours are disabled."""
        sn, _ = _make_notifier(quiet_start=0, quiet_end=0)
        sn._now_hour = lambda: 0  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is False
        sn._now_hour = lambda: 12  # type: ignore[method-assign]
        assert sn._is_quiet_hours() is False
