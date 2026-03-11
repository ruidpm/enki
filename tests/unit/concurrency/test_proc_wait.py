"""Tests for C-08: Missing await proc.wait() after proc.kill() in claude_code.py."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.claude_code import RunClaudeCodeTool


def _make_timeout_proc() -> MagicMock:
    """Create a mock proc that times out on communicate."""
    proc = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()  # must be awaited after kill
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    return proc


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


@pytest.mark.asyncio
async def test_proc_wait_called_after_kill_on_timeout(
    tool: RunClaudeCodeTool, notifier: MagicMock
) -> None:
    """After proc.kill(), proc.wait() must be awaited to prevent zombie processes."""
    proc = _make_timeout_proc()

    with patch(
        "src.tools.claude_code.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ):
        await tool._run_background("t01", "task")

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_proc_wait_called_after_kill_in_pipeline_implement(
    tmp_path: Path,
) -> None:
    """RunPipelineTool._run_implement also calls proc.kill on timeout — verify proc.wait()."""
    from src.tools.run_pipeline import RunPipelineTool

    proc = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

    notifier = AsyncMock()
    notifier.ask_single_confirm = AsyncMock(return_value=True)
    notifier.send = AsyncMock()
    notifier.ask_free_text = AsyncMock(return_value=None)

    from src.pipeline.store import PipelineStore
    from src.teams.store import TeamsStore
    from src.workspaces.store import WorkspaceStore

    pipeline_store = PipelineStore(tmp_path / "pipelines.db")
    workspace_store = WorkspaceStore(tmp_path / "ws.db")
    teams_store = TeamsStore(tmp_path / "teams.db")

    config = MagicMock()
    config.haiku_model = "claude-haiku-4-5-20251001"

    tool = RunPipelineTool(
        notifier=notifier,
        pipeline_store=pipeline_store,
        workspace_store=workspace_store,
        teams_store=teams_store,
        config=config,
        tool_registry={},
    )

    with patch(
        "src.tools.run_pipeline.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ), pytest.raises(RuntimeError, match="timed out"):
        await tool._run_implement("p1", "task", str(tmp_path), "python", {})

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()
