"""Tests for workspace_id support in RunClaudeCodeTool."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.claude_code import RunClaudeCodeTool
from src.workspaces.store import TrustLevel, WorkspaceStore


@pytest.fixture
def notifier() -> MagicMock:
    n = MagicMock()
    n.ask_single_confirm = AsyncMock(return_value=True)
    n.send = AsyncMock()
    return n


@pytest.fixture
def ws_store(tmp_path: Path) -> WorkspaceStore:
    store = WorkspaceStore(tmp_path / "ws.db")
    store.add(
        "myrepo",
        name="My Repo",
        local_path=str(tmp_path / "myrepo"),
        language="typescript",
        trust_level=TrustLevel.PROPOSE,
    )
    (tmp_path / "myrepo").mkdir()
    return store


@pytest.fixture
def tool(tmp_path: Path, notifier: MagicMock, ws_store: WorkspaceStore) -> RunClaudeCodeTool:
    t = RunClaudeCodeTool(notifier=notifier, project_dir=tmp_path, workspace_store=ws_store)
    t._last_spawn = 0.0
    return t


def _make_proc(returncode: int = 0, stdout: bytes = b"ok", stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


def _mock_spawn_seq(*procs: MagicMock) -> AsyncMock:
    return AsyncMock(side_effect=list(procs))


# ---------------------------------------------------------------------------
# workspace_id routes CCC to workspace directory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_id_runs_ccc_in_workspace_dir(
    tool: RunClaudeCodeTool, tmp_path: Path
) -> None:
    workspace_path = str(tmp_path / "myrepo")
    claude_proc = _make_proc()
    diff_proc = _make_proc(stdout=b"")

    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(claude_proc, diff_proc)) as mock_exec:
        await tool._run_background("job1", "add feature", workspace_path=workspace_path)

    first_call = mock_exec.call_args_list[0]
    assert first_call[1].get("cwd") == workspace_path


@pytest.mark.asyncio
async def test_no_workspace_id_uses_project_dir(
    tool: RunClaudeCodeTool, tmp_path: Path
) -> None:
    claude_proc = _make_proc()
    diff_proc = _make_proc(stdout=b"")

    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(claude_proc, diff_proc)) as mock_exec:
        await tool._run_background("job1", "add feature", workspace_path=None)

    first_call = mock_exec.call_args_list[0]
    assert first_call[1].get("cwd") == str(tmp_path)


# ---------------------------------------------------------------------------
# Temp CLAUDE.md injection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_temp_claude_md_written_before_ccc_and_removed_after(
    tool: RunClaudeCodeTool, tmp_path: Path
) -> None:
    workspace_dir = tmp_path / "myrepo"
    workspace_dir.mkdir(exist_ok=True)
    claude_md = workspace_dir / "CLAUDE.md"

    assert not claude_md.exists()

    written_during: list[bool] = []

    async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
        # Check if CLAUDE.md exists at call time
        written_during.append(claude_md.exists())
        proc = _make_proc()
        return proc

    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", side_effect=fake_exec):
        await tool._run_background(
            "job1", "add feature",
            workspace_path=str(workspace_dir),
            language="typescript",
        )

    assert written_during[0] is True, "CLAUDE.md must exist when CCC runs"
    assert not claude_md.exists(), "CLAUDE.md must be removed after CCC completes"


@pytest.mark.asyncio
async def test_temp_claude_md_contains_language_rules(
    tool: RunClaudeCodeTool, tmp_path: Path
) -> None:
    workspace_dir = tmp_path / "myrepo"
    workspace_dir.mkdir(exist_ok=True)
    claude_md = workspace_dir / "CLAUDE.md"

    content_written: list[str] = []

    async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
        if claude_md.exists():
            content_written.append(claude_md.read_text())
        return _make_proc()

    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", side_effect=fake_exec):
        await tool._run_background(
            "job1", "add feature",
            workspace_path=str(workspace_dir),
            language="typescript",
        )

    assert content_written, "CLAUDE.md should have been written"
    assert "typescript" in content_written[0].lower() or "TypeScript" in content_written[0]


@pytest.mark.asyncio
async def test_temp_claude_md_removed_even_on_ccc_error(
    tool: RunClaudeCodeTool, tmp_path: Path
) -> None:
    workspace_dir = tmp_path / "myrepo"
    workspace_dir.mkdir(exist_ok=True)
    claude_md = workspace_dir / "CLAUDE.md"

    claude_proc = _make_proc(returncode=1, stderr=b"error")

    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(claude_proc)):
        await tool._run_background(
            "job1", "bad task",
            workspace_path=str(workspace_dir),
            language="python",
        )

    assert not claude_md.exists(), "CLAUDE.md must be cleaned up even on error"


@pytest.mark.asyncio
async def test_existing_claude_md_preserved(
    tool: RunClaudeCodeTool, tmp_path: Path
) -> None:
    """If a CLAUDE.md already exists in the workspace, don't overwrite it."""
    workspace_dir = tmp_path / "myrepo"
    workspace_dir.mkdir(exist_ok=True)
    claude_md = workspace_dir / "CLAUDE.md"
    original = "# Existing project instructions\nDo not change me.\n"
    claude_md.write_text(original)

    claude_proc = _make_proc()
    diff_proc = _make_proc(stdout=b"")

    with patch("src.tools.claude_code.asyncio.create_subprocess_exec", _mock_spawn_seq(claude_proc, diff_proc)):
        await tool._run_background(
            "job1", "add feature",
            workspace_path=str(workspace_dir),
            language="python",
        )

    assert claude_md.read_text() == original, "Existing CLAUDE.md must not be modified"


# ---------------------------------------------------------------------------
# execute() resolves workspace_id → workspace_path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_with_valid_workspace_id(
    tool: RunClaudeCodeTool, notifier: MagicMock, tmp_path: Path
) -> None:
    with patch.object(tool, "_run_background", new=AsyncMock()) as mock_bg:
        result = await tool.execute(
            task="add a feature", reason="test", workspace_id="myrepo"
        )
        await asyncio.sleep(0)

    assert "job" in result.lower() or "background" in result.lower()
    call_kwargs = mock_bg.call_args[1]
    assert call_kwargs.get("workspace_path") is not None
    assert "myrepo" in call_kwargs["workspace_path"]


@pytest.mark.asyncio
async def test_execute_with_unknown_workspace_id_returns_error(
    tool: RunClaudeCodeTool,
) -> None:
    result = await tool.execute(
        task="add feature", reason="test", workspace_id="nonexistent"
    )
    assert "error" in result.lower() or "not found" in result.lower()


@pytest.mark.asyncio
async def test_execute_without_workspace_id_uses_project_dir(
    tool: RunClaudeCodeTool,
) -> None:
    with patch.object(tool, "_run_background", new=AsyncMock()) as mock_bg:
        await tool.execute(task="add feature", reason="test")
        await asyncio.sleep(0)

    call_kwargs = mock_bg.call_args[1]
    assert call_kwargs.get("workspace_path") is None
