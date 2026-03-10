"""Tests for smart restart — Docker vs local process restart."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock
import time

import pytest

from src.tools.restart import RequestRestartTool, is_running_in_docker
import src.tools.restart as restart_module


@pytest.fixture(autouse=True)
def reset_cooldown() -> None:
    restart_module._last_restart = 0.0


@pytest.fixture
def confirmed_notifier() -> AsyncMock:
    n = AsyncMock()
    n.ask_double_confirm = AsyncMock(return_value=True)
    n.send = AsyncMock()
    return n


def test_is_running_in_docker_true(tmp_path: object) -> None:
    with patch("pathlib.Path.exists", return_value=True):
        assert is_running_in_docker() is True


def test_is_running_in_docker_false() -> None:
    with patch("pathlib.Path.exists", return_value=False):
        assert is_running_in_docker() is False


@pytest.mark.asyncio
async def test_restart_in_docker_calls_compose(confirmed_notifier: AsyncMock) -> None:
    tool = RequestRestartTool(confirmed_notifier)
    with patch("src.tools.restart.is_running_in_docker", return_value=True), \
         patch("src.tools.restart.subprocess.Popen") as mock_popen:
        result = await tool.execute(reason="test", changes_summary="x")
    mock_popen.assert_called_once_with(["docker", "compose", "restart", "assistant"])
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
