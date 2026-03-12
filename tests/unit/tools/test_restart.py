"""Tests for RequestRestartTool — double-confirm and cooldown."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.restart import _DEFAULT_COOLDOWN, RequestRestartTool


@pytest.fixture
def confirmed_notifier() -> AsyncMock:
    n = AsyncMock()
    n.ask_double_confirm = AsyncMock(return_value=True)
    n.send = AsyncMock()
    return n


@pytest.fixture
def denied_notifier() -> AsyncMock:
    n = AsyncMock()
    n.ask_double_confirm = AsyncMock(return_value=False)
    n.send = AsyncMock()
    return n


@pytest.mark.asyncio
async def test_restart_requires_confirmation(denied_notifier: AsyncMock) -> None:
    tool = RequestRestartTool(denied_notifier)
    result = await tool.execute(reason="apply patch", changes_summary="fixed bug")
    assert "cancelled" in result.lower()
    denied_notifier.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_confirmed_sends_sigterm_in_docker(confirmed_notifier: AsyncMock) -> None:
    tool = RequestRestartTool(confirmed_notifier)
    with patch("src.tools.restart.is_running_in_docker", return_value=True), patch("src.tools.restart.os.kill") as mock_kill:
        result = await tool.execute(reason="apply patch", changes_summary="new tool added")
    import os
    import signal

    mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
    assert "initiated" in result.lower()


@pytest.mark.asyncio
async def test_restart_sends_notification_before_restart(confirmed_notifier: AsyncMock) -> None:
    tool = RequestRestartTool(confirmed_notifier)
    with patch("src.tools.restart.is_running_in_docker", return_value=True), patch("src.tools.restart.os.kill"):
        await tool.execute(reason="patch", changes_summary="change")
    confirmed_notifier.send.assert_awaited_once()
    msg = confirmed_notifier.send.call_args[0][0]
    assert "restart" in msg.lower()


@pytest.mark.asyncio
async def test_cooldown_blocks_rapid_restart(confirmed_notifier: AsyncMock) -> None:
    tool = RequestRestartTool(confirmed_notifier)
    tool._last_restart = time.time()  # instance state — no module global anymore
    result = await tool.execute(reason="again", changes_summary="more changes")
    assert "cooldown" in result.lower()


@pytest.mark.asyncio
async def test_cooldown_allows_after_expiry(confirmed_notifier: AsyncMock) -> None:
    tool = RequestRestartTool(confirmed_notifier)
    tool._last_restart = time.time() - _DEFAULT_COOLDOWN - 1
    with patch("src.tools.restart.is_running_in_docker", return_value=True), patch("src.tools.restart.os.kill"):
        result = await tool.execute(reason="ok now", changes_summary="fine")
    assert "initiated" in result.lower()
