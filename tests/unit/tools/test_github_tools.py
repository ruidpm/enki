"""Tests for GitHub tools — branch protection and CLI dispatch."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.tools.github_tools import (
    GitStatusTool,
    GitDiffTool,
    GitCommitTool,
    GitPushBranchTool,
    CreatePRTool,
)


def _mock_proc(returncode: int, stdout: str, stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    return proc


@pytest.mark.asyncio
async def test_git_push_blocks_main() -> None:
    tool = GitPushBranchTool()
    result = await tool.execute(branch="main")
    assert "BLOCKED" in result


@pytest.mark.asyncio
async def test_git_push_blocks_master() -> None:
    tool = GitPushBranchTool()
    result = await tool.execute(branch="master")
    assert "BLOCKED" in result


@pytest.mark.asyncio
async def test_git_push_allows_feature_branch() -> None:
    tool = GitPushBranchTool()
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec",
               return_value=_mock_proc(0, "Branch pushed.")):
        result = await tool.execute(branch="feature/new-tool")
    assert "BLOCKED" not in result


@pytest.mark.asyncio
async def test_create_pr_blocks_from_main() -> None:
    tool = CreatePRTool()
    result = await tool.execute(title="bad PR", branch="main")
    assert "BLOCKED" in result


@pytest.mark.asyncio
async def test_create_pr_blocks_from_master() -> None:
    tool = CreatePRTool()
    result = await tool.execute(title="bad PR", branch="master")
    assert "BLOCKED" in result


@pytest.mark.asyncio
async def test_create_pr_allows_feature_branch() -> None:
    tool = CreatePRTool()
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec",
               return_value=_mock_proc(0, "https://github.com/user/repo/pull/1")):
        result = await tool.execute(title="Add feature", branch="feature/x")
    assert "BLOCKED" not in result


@pytest.mark.asyncio
async def test_git_status_returns_output() -> None:
    tool = GitStatusTool()
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec",
               return_value=_mock_proc(0, "M src/agent.py")):
        result = await tool.execute()
    assert "src/agent.py" in result


@pytest.mark.asyncio
async def test_git_status_clean() -> None:
    tool = GitStatusTool()
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec",
               return_value=_mock_proc(0, "")):
        result = await tool.execute()
    assert "clean" in result.lower()
