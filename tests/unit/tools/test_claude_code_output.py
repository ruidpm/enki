"""Tests for gist + Enki summary output handling in RunClaudeCodeTool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.claude_code import _GIST_THRESHOLD, RunClaudeCodeTool


@pytest.fixture
def notifier() -> MagicMock:
    n = MagicMock()
    n.send = AsyncMock()
    n.ask_single_confirm = AsyncMock(return_value=True)
    return n


@pytest.fixture
def tool(notifier: MagicMock, tmp_path: Path) -> RunClaudeCodeTool:
    return RunClaudeCodeTool(notifier=notifier, project_dir=tmp_path)


def _mock_agent(summary: str = "• Change A\n• Change B") -> MagicMock:
    agent = MagicMock()
    agent.run_turn = AsyncMock(return_value=summary)
    return agent


def _mock_gist_proc(url: str = "https://gist.github.com/abc123") -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(url.encode(), b""))
    return proc


def _mock_gist_fail_proc() -> MagicMock:
    proc = MagicMock()
    proc.returncode = 1
    proc.communicate = AsyncMock(return_value=(b"", b"error"))
    return proc


@pytest.mark.asyncio
async def test_short_output_sent_directly(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    """Output under threshold → sent directly, no gist."""
    short = "x" * (_GIST_THRESHOLD - 1)
    await tool._send_output("abc123", short)
    notifier.send.assert_called_once()
    msg = notifier.send.call_args[0][0]
    assert "gist" not in msg.lower()
    assert "abc123" in msg


@pytest.mark.asyncio
async def test_short_output_without_agent_sent_directly(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    """Long output but no agent → falls back to truncated direct send."""
    long_output = "x" * (_GIST_THRESHOLD + 100)
    # tool._agent is None by default
    await tool._send_output("job1", long_output)
    notifier.send.assert_called_once()
    msg = notifier.send.call_args[0][0]
    assert "job1" in msg


@pytest.mark.asyncio
async def test_long_output_creates_gist_and_summarizes(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    """Long output + agent → gist created, summary sent with URL."""
    tool.set_agent(_mock_agent("• Added feature X\n• Fixed bug Y"))
    long_output = "A" * (_GIST_THRESHOLD + 100)

    with patch(
        "src.tools.claude_code.asyncio.create_subprocess_exec",
        return_value=_mock_gist_proc("https://gist.github.com/xyz"),
    ):
        await tool._send_output("job42", long_output)

    notifier.send.assert_called_once()
    msg = notifier.send.call_args[0][0]
    assert "https://gist.github.com/xyz" in msg
    assert "Added feature X" in msg
    assert "job42" in msg


@pytest.mark.asyncio
async def test_gist_failure_degrades_gracefully(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    """Gist creation fails → still send summary with failure note."""
    tool.set_agent(_mock_agent("• Work done"))
    long_output = "B" * (_GIST_THRESHOLD + 100)

    with patch(
        "src.tools.claude_code.asyncio.create_subprocess_exec",
        return_value=_mock_gist_fail_proc(),
    ):
        await tool._send_output("job99", long_output)

    notifier.send.assert_called_once()
    msg = notifier.send.call_args[0][0]
    assert "gist creation failed" in msg
    assert "Work done" in msg


@pytest.mark.asyncio
async def test_agent_summary_failure_degrades_gracefully(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    """If agent.run_turn raises → fall back to raw truncation."""
    agent = MagicMock()
    agent.run_turn = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    tool.set_agent(agent)
    long_output = "C" * (_GIST_THRESHOLD + 100)

    with patch(
        "src.tools.claude_code.asyncio.create_subprocess_exec",
        return_value=_mock_gist_proc(),
    ):
        await tool._send_output("job77", long_output)

    notifier.send.assert_called_once()
    msg = notifier.send.call_args[0][0]
    # Should still have the gist URL even if summary failed
    assert "https://gist.github.com/abc123" in msg


@pytest.mark.asyncio
async def test_create_gist_returns_url_on_success(tool: RunClaudeCodeTool) -> None:
    with patch(
        "src.tools.claude_code.asyncio.create_subprocess_exec",
        return_value=_mock_gist_proc("https://gist.github.com/newgist"),
    ):
        url = await tool._create_gist("some content", "test desc")
    assert url == "https://gist.github.com/newgist"


@pytest.mark.asyncio
async def test_create_gist_returns_none_on_failure(tool: RunClaudeCodeTool) -> None:
    with patch(
        "src.tools.claude_code.asyncio.create_subprocess_exec",
        return_value=_mock_gist_fail_proc(),
    ):
        url = await tool._create_gist("some content", "test desc")
    assert url is None
