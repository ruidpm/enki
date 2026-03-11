"""Tests for smart restart — Docker vs local process restart."""
from __future__ import annotations

import os
import signal
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.restart import RequestRestartTool, is_running_in_docker


@pytest.fixture
def confirmed_notifier() -> AsyncMock:
    n = AsyncMock()
    n.ask_double_confirm = AsyncMock(return_value=True)
    n.send = AsyncMock()
    return n


def test_is_running_in_docker_true() -> None:
    with patch("pathlib.Path.exists", return_value=True):
        assert is_running_in_docker() is True


def test_is_running_in_docker_false() -> None:
    with patch("pathlib.Path.exists", return_value=False):
        assert is_running_in_docker() is False


@pytest.mark.asyncio
async def test_restart_in_docker_sends_sigterm(confirmed_notifier: AsyncMock) -> None:
    tool = RequestRestartTool(confirmed_notifier)
    with patch("src.tools.restart.is_running_in_docker", return_value=True), \
         patch("src.tools.restart.os.kill") as mock_kill:
        result = await tool.execute(reason="test", changes_summary="x")
    mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
    assert "initiated" in result.lower()


@pytest.mark.asyncio
async def test_restart_locally_calls_execv(confirmed_notifier: AsyncMock) -> None:
    tool = RequestRestartTool(confirmed_notifier)
    with patch("src.tools.restart.is_running_in_docker", return_value=False), \
         patch("src.tools.restart.os.execv") as mock_execv, \
         patch("src.tools.restart.sys.executable", "/usr/bin/python3"), \
         patch("src.tools.restart.sys.argv", ["main.py", "chat"]):
        result = await tool.execute(reason="test", changes_summary="x")
    mock_execv.assert_called_once_with("/usr/bin/python3", ["/usr/bin/python3", "main.py", "chat"])
    assert "initiated" in result.lower()
