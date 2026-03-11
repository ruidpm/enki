"""Tests for ListWorkspacesTool and ManageWorkspaceTool."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workspaces.store import WorkspaceStore, TrustLevel
from src.tools.manage_workspace import ListWorkspacesTool, ManageWorkspaceTool


@pytest.fixture
def store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path / "ws.db")


@pytest.fixture
def list_tool(store: WorkspaceStore) -> ListWorkspacesTool:
    return ListWorkspacesTool(store=store)


@pytest.fixture
def manage_tool(store: WorkspaceStore) -> ManageWorkspaceTool:
    return ManageWorkspaceTool(store=store)


# ---------------------------------------------------------------------------
# ListWorkspacesTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_empty(list_tool: ListWorkspacesTool) -> None:
    result = await list_tool.execute()
    assert "no workspaces" in result.lower() or result.strip() != ""


@pytest.mark.asyncio
async def test_list_shows_registered_workspaces(
    list_tool: ListWorkspacesTool, store: WorkspaceStore
) -> None:
    store.add("ws1", name="MyApp", local_path="/projects/myapp", language="python")
    store.add("ws2", name="Frontend", local_path="/projects/fe", language="typescript")
    result = await list_tool.execute()
    assert "MyApp" in result
    assert "Frontend" in result
    assert "python" in result
    assert "typescript" in result


@pytest.mark.asyncio
async def test_list_shows_trust_level(
    list_tool: ListWorkspacesTool, store: WorkspaceStore
) -> None:
    store.add("ws1", name="T", local_path="/t", trust_level=TrustLevel.AUTO_PUSH)
    result = await list_tool.execute()
    assert "auto_push" in result.lower() or "3" in result


# ---------------------------------------------------------------------------
# ManageWorkspaceTool — add
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_registers_workspace(
    manage_tool: ManageWorkspaceTool, store: WorkspaceStore, tmp_path: Path
) -> None:
    local_path = str(tmp_path)
    result = await manage_tool.execute(
        action="add",
        workspace_id="proj1",
        name="Project One",
        local_path=local_path,
        language="python",
    )
    assert "proj1" in result or "Project One" in result or "added" in result.lower()
    ws = store.get("proj1")
    assert ws is not None
    assert ws["name"] == "Project One"


@pytest.mark.asyncio
async def test_add_requires_local_path_to_exist(
    manage_tool: ManageWorkspaceTool,
) -> None:
    result = await manage_tool.execute(
        action="add",
        workspace_id="x",
        name="X",
        local_path="/nonexistent/path/that/does/not/exist",
    )
    assert "error" in result.lower() or "not found" in result.lower()


@pytest.mark.asyncio
async def test_add_missing_workspace_id(manage_tool: ManageWorkspaceTool) -> None:
    result = await manage_tool.execute(action="add", name="X", local_path="/tmp")
    assert "error" in result.lower() or "required" in result.lower()


# ---------------------------------------------------------------------------
# ManageWorkspaceTool — remove
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_existing(
    manage_tool: ManageWorkspaceTool, store: WorkspaceStore
) -> None:
    store.add("ws1", name="X", local_path="/x")
    result = await manage_tool.execute(action="remove", workspace_id="ws1")
    assert "removed" in result.lower() or "ws1" in result
    assert store.get("ws1") is None


@pytest.mark.asyncio
async def test_remove_nonexistent(manage_tool: ManageWorkspaceTool) -> None:
    result = await manage_tool.execute(action="remove", workspace_id="ghost")
    assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# ManageWorkspaceTool — set_trust
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_trust_updates_level(
    manage_tool: ManageWorkspaceTool, store: WorkspaceStore
) -> None:
    store.add("ws1", name="X", local_path="/x")
    result = await manage_tool.execute(
        action="set_trust", workspace_id="ws1", trust_level=TrustLevel.AUTO_COMMIT
    )
    assert "trust" in result.lower() or "ws1" in result
    ws = store.get("ws1")
    assert ws is not None
    assert ws["trust_level"] == TrustLevel.AUTO_COMMIT


@pytest.mark.asyncio
async def test_set_trust_invalid_level(
    manage_tool: ManageWorkspaceTool, store: WorkspaceStore
) -> None:
    store.add("ws1", name="X", local_path="/x")
    result = await manage_tool.execute(
        action="set_trust", workspace_id="ws1", trust_level=99
    )
    assert "invalid" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_set_trust_unknown_workspace(manage_tool: ManageWorkspaceTool) -> None:
    result = await manage_tool.execute(
        action="set_trust", workspace_id="ghost", trust_level=TrustLevel.PROPOSE
    )
    assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# ManageWorkspaceTool — clone
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clone_runs_git_clone(
    manage_tool: ManageWorkspaceTool, tmp_path: Path
) -> None:
    dest = tmp_path / "cloned"
    with patch("src.tools.manage_workspace.asyncio.create_subprocess_exec") as mock_exec:
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = proc

        result = await manage_tool.execute(
            action="clone",
            workspace_id="myrepo",
            name="My Repo",
            git_remote="https://github.com/user/myrepo",
            local_path=str(dest),
        )

    mock_exec.assert_called_once()
    call_args = mock_exec.call_args[0]
    assert "git" in call_args
    assert "clone" in call_args
    assert "https://github.com/user/myrepo" in call_args


@pytest.mark.asyncio
async def test_clone_git_failure_returns_error(
    manage_tool: ManageWorkspaceTool, tmp_path: Path
) -> None:
    dest = tmp_path / "cloned"
    with patch("src.tools.manage_workspace.asyncio.create_subprocess_exec") as mock_exec:
        proc = MagicMock()
        proc.returncode = 128
        proc.communicate = AsyncMock(return_value=(b"", b"Repository not found"))
        mock_exec.return_value = proc

        result = await manage_tool.execute(
            action="clone",
            workspace_id="myrepo",
            name="My Repo",
            git_remote="https://github.com/user/myrepo",
            local_path=str(dest),
        )

    assert "error" in result.lower() or "failed" in result.lower()


# ---------------------------------------------------------------------------
# ManageWorkspaceTool — init
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_creates_directory_and_registers(
    manage_tool: ManageWorkspaceTool, store: WorkspaceStore, tmp_path: Path
) -> None:
    new_dir = tmp_path / "brand_new_project"
    assert not new_dir.exists()

    with patch("src.tools.manage_workspace.asyncio.create_subprocess_exec") as mock_exec:
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = proc

        result = await manage_tool.execute(
            action="init",
            workspace_id="newproj",
            name="New Project",
            local_path=str(new_dir),
            language="typescript",
        )

    assert "newproj" in result or "initialized" in result.lower()
    # Dir was created
    assert new_dir.exists()
    # git init was called in the new dir
    call_args = mock_exec.call_args[0]
    assert "git" in call_args
    assert "init" in call_args
    # Workspace registered
    ws = store.get("newproj")
    assert ws is not None
    assert ws["name"] == "New Project"


@pytest.mark.asyncio
async def test_init_existing_directory_ok(
    manage_tool: ManageWorkspaceTool, store: WorkspaceStore, tmp_path: Path
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()

    with patch("src.tools.manage_workspace.asyncio.create_subprocess_exec") as mock_exec:
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = proc

        result = await manage_tool.execute(
            action="init",
            workspace_id="existing_ws",
            name="Existing",
            local_path=str(existing),
        )

    assert "error" not in result.lower()
    assert store.get("existing_ws") is not None


@pytest.mark.asyncio
async def test_init_git_fail_returns_error(
    manage_tool: ManageWorkspaceTool, store: WorkspaceStore, tmp_path: Path
) -> None:
    new_dir = tmp_path / "fail_proj"

    with patch("src.tools.manage_workspace.asyncio.create_subprocess_exec") as mock_exec:
        proc = MagicMock()
        proc.returncode = 128
        proc.communicate = AsyncMock(return_value=(b"", b"fatal: permission denied"))
        mock_exec.return_value = proc

        result = await manage_tool.execute(
            action="init",
            workspace_id="failproj",
            name="Fail",
            local_path=str(new_dir),
        )

    assert "error" in result.lower() or "failed" in result.lower()
    assert store.get("failproj") is None


@pytest.mark.asyncio
async def test_init_missing_workspace_id(manage_tool: ManageWorkspaceTool) -> None:
    result = await manage_tool.execute(action="init", local_path="/tmp/x")
    assert "error" in result.lower() or "required" in result.lower()


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_action(manage_tool: ManageWorkspaceTool) -> None:
    result = await manage_tool.execute(action="explode", workspace_id="x")
    assert "unknown" in result.lower() or "invalid" in result.lower()


# ---------------------------------------------------------------------------
# ManageWorkspaceTool — clone URL validation + trust cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clone_rejects_ftp_url(manage_tool: ManageWorkspaceTool, tmp_path: Path) -> None:
    result = await manage_tool.execute(
        action="clone",
        workspace_id="bad",
        git_remote="ftp://example.com/repo.git",
        local_path=str(tmp_path / "dest"),
    )
    assert "error" in result.lower()
    assert "https" in result.lower() or "git@" in result.lower()


@pytest.mark.asyncio
async def test_clone_rejects_bare_path(manage_tool: ManageWorkspaceTool, tmp_path: Path) -> None:
    result = await manage_tool.execute(
        action="clone",
        workspace_id="bad",
        git_remote="/home/user/local-repo",
        local_path=str(tmp_path / "dest"),
    )
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_clone_rejects_file_scheme(manage_tool: ManageWorkspaceTool, tmp_path: Path) -> None:
    result = await manage_tool.execute(
        action="clone",
        workspace_id="bad",
        git_remote="file:///home/user/repo",
        local_path=str(tmp_path / "dest"),
    )
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_clone_caps_trust_at_propose(
    manage_tool: ManageWorkspaceTool, store: WorkspaceStore, tmp_path: Path
) -> None:
    """Even if trust_level=4 is requested for clone, it's capped at PROPOSE (1)."""
    dest = tmp_path / "cloned"
    with patch("src.tools.manage_workspace.asyncio.create_subprocess_exec") as mock_exec:
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = proc

        await manage_tool.execute(
            action="clone",
            workspace_id="highrepo",
            git_remote="https://github.com/user/repo",
            local_path=str(dest),
            trust_level=TrustLevel.TRUSTED,  # 4 — should be capped
        )

    ws = store.get("highrepo")
    assert ws is not None
    assert ws["trust_level"] <= TrustLevel.PROPOSE  # capped at 1


@pytest.mark.asyncio
async def test_clone_ssh_url_allowed(manage_tool: ManageWorkspaceTool, tmp_path: Path) -> None:
    """git@ SSH URLs should pass URL validation."""
    dest = tmp_path / "ssh_clone"
    with patch("src.tools.manage_workspace.asyncio.create_subprocess_exec") as mock_exec:
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_exec.return_value = proc

        result = await manage_tool.execute(
            action="clone",
            workspace_id="sshrepo",
            git_remote="git@github.com:user/repo.git",
            local_path=str(dest),
        )

    assert "error" not in result.lower() or "clone" in result.lower()
