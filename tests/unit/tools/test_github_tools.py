"""Tests for GitHub tools — branch protection and CLI dispatch."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.github_tools import (
    CreatePRTool,
    GitCommitTool,
    GitPushBranchTool,
    GitStatusTool,
)
from src.workspaces.store import TrustLevel, WorkspaceStore


@pytest.fixture
def workspace_store(tmp_path: Path) -> WorkspaceStore:
    store = WorkspaceStore(tmp_path / "ws.db")
    ws_path = tmp_path / "myapp"
    ws_path.mkdir()
    store.add("read_only_ws", name="ReadOnly", local_path=str(ws_path), trust_level=TrustLevel.READ_ONLY)
    store.add("propose_ws", name="Propose", local_path=str(ws_path), trust_level=TrustLevel.PROPOSE)
    store.add("auto_push_ws", name="AutoPush", local_path=str(ws_path), trust_level=TrustLevel.AUTO_PUSH)
    return store


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


# ---------------------------------------------------------------------------
# Trust enforcement — workspace-aware tools
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_commit_blocked_for_read_only_workspace(workspace_store: WorkspaceStore) -> None:
    tool = GitCommitTool(workspace_store=workspace_store)
    result = await tool.execute(
        workspace_id="read_only_ws", message="test commit", files=["."]
    )
    assert "BLOCKED" in result
    assert "trust" in result.lower()


@pytest.mark.asyncio
async def test_commit_allowed_for_propose_workspace(workspace_store: WorkspaceStore) -> None:
    tool = GitCommitTool(workspace_store=workspace_store)
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec",
               return_value=_mock_proc(0, "[main abc1234] test commit")):
        result = await tool.execute(
            workspace_id="propose_ws", message="test commit", files=["."]
        )
    assert "BLOCKED" not in result


@pytest.mark.asyncio
async def test_push_blocked_for_read_only_workspace(workspace_store: WorkspaceStore) -> None:
    tool = GitPushBranchTool(workspace_store=workspace_store)
    result = await tool.execute(workspace_id="read_only_ws", branch="feat/new")
    assert "BLOCKED" in result
    assert "trust" in result.lower()


@pytest.mark.asyncio
async def test_push_blocked_for_propose_workspace(workspace_store: WorkspaceStore) -> None:
    """push requires AUTO_PUSH (3); PROPOSE (1) is not enough."""
    tool = GitPushBranchTool(workspace_store=workspace_store)
    result = await tool.execute(workspace_id="propose_ws", branch="feat/new")
    assert "BLOCKED" in result
    assert "trust" in result.lower()


@pytest.mark.asyncio
async def test_push_allowed_for_auto_push_workspace(workspace_store: WorkspaceStore) -> None:
    tool = GitPushBranchTool(workspace_store=workspace_store)
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec",
               return_value=_mock_proc(0, "Branch 'feat/new' set up to track...")):
        result = await tool.execute(workspace_id="auto_push_ws", branch="feat/new")
    assert "BLOCKED" not in result


@pytest.mark.asyncio
async def test_create_pr_blocked_for_read_only_workspace(workspace_store: WorkspaceStore) -> None:
    tool = CreatePRTool(workspace_store=workspace_store)
    result = await tool.execute(
        workspace_id="read_only_ws", title="My PR", branch="feat/x"
    )
    assert "BLOCKED" in result
    assert "trust" in result.lower()


@pytest.mark.asyncio
async def test_commit_no_workspace_id_uses_cwd(workspace_store: WorkspaceStore) -> None:
    """No workspace_id → operates on assistant's own repo, no trust check."""
    tool = GitCommitTool(workspace_store=workspace_store)
    with patch("src.tools.github_tools.asyncio.create_subprocess_exec",
               return_value=_mock_proc(0, "[main abc] commit")):
        result = await tool.execute(message="local commit", files=["."])
    assert "BLOCKED" not in result


@pytest.mark.asyncio
async def test_commit_unknown_workspace_returns_error(workspace_store: WorkspaceStore) -> None:
    tool = GitCommitTool(workspace_store=workspace_store)
    result = await tool.execute(
        workspace_id="ghost_ws", message="test", files=["."]
    )
    assert "error" in result.lower() or "not found" in result.lower()
