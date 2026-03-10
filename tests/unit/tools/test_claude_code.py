"""Tests for RunClaudeCodeTool — background job design."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.tools.claude_code import RunClaudeCodeTool


def _make_proc(returncode: int = 0, stdout: bytes = b"ok", stderr: bytes = b"") -> MagicMock:
    """Build a mock subprocess result."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


def _mock_spawn_seq(*procs: MagicMock):  # type: ignore[no-untyped-def]
    """Return an AsyncMock for create_subprocess_exec that yields procs in order."""
    return AsyncMock(side_effect=list(procs))


@pytest.fixture
def notifier() -> MagicMock:
    n = MagicMock()
    n.ask_single_confirm = AsyncMock(return_value=True)
    n.send = AsyncMock()
    return n


@pytest.fixture
def tool(tmp_path: Path, notifier: MagicMock) -> RunClaudeCodeTool:
    t = RunClaudeCodeTool(notifier=notifier, project_dir=tmp_path)
    t._last_spawn = 0.0
    return t


# ---------------------------------------------------------------------------
# Confirmation gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancelled_when_user_denies(tmp_path: Path) -> None:
    n = MagicMock()
    n.ask_single_confirm = AsyncMock(return_value=False)
    n.send = AsyncMock()
    t = RunClaudeCodeTool(notifier=n, project_dir=tmp_path)
    result = await t.execute(task="add a hello world function", reason="test")
    assert "cancel" in result.lower()
    n.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_double_confirm_called_with_reason(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    with patch.object(tool, "_run_background", new=AsyncMock()):
        await tool.execute(task="add feature", reason="user requested new feature")
        # Let the create_task fire
        await asyncio.sleep(0)

    notifier.ask_single_confirm.assert_awaited_once()
    assert "user requested new feature" in str(notifier.ask_single_confirm.call_args)


@pytest.mark.asyncio
async def test_execute_returns_immediately_with_job_id(tool: RunClaudeCodeTool) -> None:
    """execute() must return before the background job finishes."""
    with patch.object(tool, "_run_background", new=AsyncMock()):
        result = await tool.execute(task="add feature", reason="test")
        await asyncio.sleep(0)

    assert "background" in result.lower() or "job" in result.lower()


# ---------------------------------------------------------------------------
# Background job: _run_background success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_background_sends_result_on_success(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    claude_proc = _make_proc(stdout=b"Created file src/tools/hello.py")
    diff_proc = _make_proc(stdout=b"diff --git a/src/tools/hello.py b/src/tools/hello.py\n+++ b/src/tools/hello.py\n+def hello(): pass")
    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(claude_proc, diff_proc)):
        await tool._run_background("abc12345", "add hello tool")

    notifier.send.assert_awaited_once()
    sent = notifier.send.call_args[0][0]
    assert "Created file" in sent
    assert "abc12345" in sent


@pytest.mark.asyncio
async def test_background_uses_hardcoded_command(tool: RunClaudeCodeTool) -> None:
    """claude binary and flags must be hardcoded — task is the only variable part."""
    claude_proc = _make_proc()
    diff_proc = _make_proc(stdout=b"")
    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(claude_proc, diff_proc)) as mock_spawn:
        await tool._run_background("xyz", "my task")

    first_call_args = mock_spawn.call_args_list[0][0]
    assert first_call_args[0] == "claude"
    assert "my task" in first_call_args


@pytest.mark.asyncio
async def test_background_uses_project_dir(tool: RunClaudeCodeTool, tmp_path: Path) -> None:
    claude_proc = _make_proc()
    diff_proc = _make_proc(stdout=b"")
    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(claude_proc, diff_proc)) as mock_spawn:
        await tool._run_background("xyz", "task")

    assert mock_spawn.call_args_list[0][1].get("cwd") == str(tmp_path)


# ---------------------------------------------------------------------------
# Background job: error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_background_notifies_on_nonzero_exit(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    claude_proc = _make_proc(returncode=1, stdout=b"", stderr=b"something went wrong")
    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(claude_proc)):
        await tool._run_background("err1", "bad task")

    sent = notifier.send.call_args[0][0]
    assert "ERROR" in sent
    assert "err1" in sent


@pytest.mark.asyncio
async def test_background_notifies_on_timeout(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    proc = MagicMock()
    proc.kill = MagicMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(proc)):
        await tool._run_background("t01", "infinite task")

    proc.kill.assert_called_once()
    sent = notifier.send.call_args[0][0]
    assert "TIMEOUT" in sent
    assert "t01" in sent


@pytest.mark.asyncio
async def test_background_notifies_on_spawn_failure(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", side_effect=FileNotFoundError("claude not found")):
        await tool._run_background("f01", "any task")

    sent = notifier.send.call_args[0][0]
    assert "Failed" in sent or "f01" in sent


@pytest.mark.asyncio
async def test_background_includes_diff_in_notification(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    claude_proc = _make_proc(stdout=b"done")
    diff_proc = _make_proc(stdout=b"diff --git a/src/tools/foo.py\n+++ b/src/tools/foo.py\n+def foo(): pass")
    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(claude_proc, diff_proc)):
        await tool._run_background("d01", "add foo")

    sent = notifier.send.call_args[0][0]
    assert "Diff" in sent or "diff" in sent
    assert "src/tools/foo.py" in sent


@pytest.mark.asyncio
async def test_background_flags_protected_path_violation(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    claude_proc = _make_proc(stdout=b"done")
    # Simulate CCC touching src/guardrails/scope_check.py
    diff_proc = _make_proc(
        stdout=b"diff --git a/src/guardrails/scope_check.py b/src/guardrails/scope_check.py\n"
               b"+++ b/src/guardrails/scope_check.py\n"
               b'+    "www.evil.com",\n'
    )
    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(claude_proc, diff_proc)):
        await tool._run_background("v01", "add evil host")

    sent = notifier.send.call_args[0][0]
    assert "VIOLATION" in sent or "PROTECTED" in sent
    assert "src/guardrails/" in sent


@pytest.mark.asyncio
async def test_protected_paths_prepended_to_task(tool: RunClaudeCodeTool, notifier: MagicMock) -> None:
    """Task sent to CCC must include the protected paths restriction."""
    with patch.object(tool, "_run_background", new=AsyncMock()) as mock_bg:
        await tool.execute(task="do something", reason="test")
        await asyncio.sleep(0)

    actual_task = mock_bg.call_args[0][1]
    assert "src/guardrails/" in actual_task
    assert "do something" in actual_task


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cooldown_blocks_rapid_respawn(tool: RunClaudeCodeTool) -> None:
    tool._last_spawn = time.time()
    result = await tool.execute(task="task", reason="test")
    assert "cooldown" in result.lower() or "BLOCKED" in result
