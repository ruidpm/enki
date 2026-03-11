"""Tests for workspace-aware git/GitHub tools."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.workspaces.store import WorkspaceStore
from src.tools.github_tools import (
    GitStatusTool,
    GitDiffTool,
    GitCommitTool,
    GitPushBranchTool,
    CreatePRTool,
)


@pytest.fixture
def ws_store(tmp_path: Path) -> WorkspaceStore:
    store = WorkspaceStore(tmp_path / "ws.db")
    store.add("ws1", name="MyApp", local_path=str(tmp_path / "myapp"), language="python")
    (tmp_path / "myapp").mkdir()
    return store


def _make_proc(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    return proc


# ---------------------------------------------------------------------------
# GitStatusTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_status_no_workspace(ws_store: WorkspaceStore) -> None:
    tool = GitStatusTool(workspace_store=ws_store)
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec", return_value=_make_proc(stdout="M src/foo.py")) as mock:
        result = await tool.execute()
    assert "foo.py" in result
    args = mock.call_args[0]
    # cwd should NOT be set (assistant repo)
    assert mock.call_args.kwargs.get("cwd") is None


@pytest.mark.asyncio
async def test_git_status_with_workspace(ws_store: WorkspaceStore, tmp_path: Path) -> None:
    tool = GitStatusTool(workspace_store=ws_store)
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec", return_value=_make_proc(stdout="M app.ts")) as mock:
        result = await tool.execute(workspace_id="ws1")
    assert "app.ts" in result
    assert mock.call_args.kwargs["cwd"] == str(tmp_path / "myapp")


@pytest.mark.asyncio
async def test_git_status_unknown_workspace(ws_store: WorkspaceStore) -> None:
    tool = GitStatusTool(workspace_store=ws_store)
    result = await tool.execute(workspace_id="ghost")
    assert "error" in result.lower() or "not found" in result.lower()


# ---------------------------------------------------------------------------
# GitDiffTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_diff_with_workspace(ws_store: WorkspaceStore, tmp_path: Path) -> None:
    tool = GitDiffTool(workspace_store=ws_store)
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec", return_value=_make_proc(stdout="diff output")) as mock:
        result = await tool.execute(workspace_id="ws1")
    assert "diff output" in result
    assert mock.call_args.kwargs["cwd"] == str(tmp_path / "myapp")


# ---------------------------------------------------------------------------
# GitCommitTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_commit_with_workspace(ws_store: WorkspaceStore, tmp_path: Path) -> None:
    tool = GitCommitTool(workspace_store=ws_store)
    add_proc = _make_proc(stdout="")
    commit_proc = _make_proc(stdout="[feat/x abc1234] add thing")

    with patch("src.tools.github_tools.asyncio.create_subprocess_exec", side_effect=[add_proc, commit_proc]) as mock:
        result = await tool.execute(workspace_id="ws1", message="add thing", files=["app.ts"])

    assert "feat/x" in result or "add thing" in result
    # Both calls should use the workspace cwd
    for call in mock.call_args_list:
        assert call.kwargs["cwd"] == str(tmp_path / "myapp")


# ---------------------------------------------------------------------------
# GitPushBranchTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_push_branch_with_workspace(tmp_path: Path) -> None:
    from src.workspaces.store import TrustLevel
    store = WorkspaceStore(tmp_path / "ws2.db")
    (tmp_path / "myapp2").mkdir()
    store.add("ws2", name="MyApp2", local_path=str(tmp_path / "myapp2"), trust_level=TrustLevel.AUTO_PUSH)
    tool = GitPushBranchTool(workspace_store=store)
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec", return_value=_make_proc(stdout="pushed")) as mock:
        result = await tool.execute(workspace_id="ws2", branch="feat/login")
    assert "pushed" in result
    assert mock.call_args.kwargs["cwd"] == str(tmp_path / "myapp2")


@pytest.mark.asyncio
async def test_git_push_branch_blocks_main_regardless_of_workspace(ws_store: WorkspaceStore) -> None:
    tool = GitPushBranchTool(workspace_store=ws_store)
    result = await tool.execute(workspace_id="ws1", branch="main")
    assert "blocked" in result.lower()


# ---------------------------------------------------------------------------
# CreatePRTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_pr_with_workspace(ws_store: WorkspaceStore, tmp_path: Path) -> None:
    tool = CreatePRTool(workspace_store=ws_store)
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec", return_value=_make_proc(stdout="https://github.com/u/r/pull/1")) as mock:
        result = await tool.execute(workspace_id="ws1", title="Add login", branch="feat/login")
    assert "github.com" in result
    # gh should be called with cwd=workspace path
    assert mock.call_args.kwargs["cwd"] == str(tmp_path / "myapp")


@pytest.mark.asyncio
async def test_create_pr_no_workspace_uses_assistant_repo() -> None:
    tool = CreatePRTool()
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec", return_value=_make_proc(stdout="https://github.com/u/r/pull/2")) as mock:
        result = await tool.execute(title="Fix bug", branch="fix/bug")
    assert "github.com" in result
    assert mock.call_args.kwargs.get("cwd") is None


# ---------------------------------------------------------------------------
# No workspace_store configured but workspace_id given
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_status_no_store_with_workspace_id() -> None:
    tool = GitStatusTool()  # no store
    result = await tool.execute(workspace_id="ws1")
    assert "error" in result.lower() or "no workspace" in result.lower()
