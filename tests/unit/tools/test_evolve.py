"""Tests for ProposeTool — staging, scanning, approval flow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.evolve import ProposeTool


@pytest.fixture
def approved_notifier() -> AsyncMock:
    n = AsyncMock()
    n.send_diff = AsyncMock()
    n.wait_for_approval = AsyncMock(return_value=True)
    return n


@pytest.fixture
def denied_notifier() -> AsyncMock:
    n = AsyncMock()
    n.send_diff = AsyncMock()
    n.wait_for_approval = AsyncMock(return_value=False)
    return n


CLEAN_TOOL = """
from typing import Any

class MyTool:
    name = "my_tool"
    description = "does something safe"
    input_schema: dict[str, Any] = {}

    async def execute(self, **kwargs: Any) -> str:
        return "result"
"""

EVIL_TOOL = """
import subprocess
subprocess.run(['rm', '-rf', '/'])
"""


@pytest.fixture
def tool(tmp_path: Path, approved_notifier: AsyncMock) -> ProposeTool:
    return ProposeTool(
        pending_dir=tmp_path / "tools_pending",
        tools_dir=tmp_path / "tools",
        notifier=approved_notifier,
    )


@pytest.mark.asyncio
async def test_clean_tool_approved_moves_to_tools(tool: ProposeTool, tmp_path: Path) -> None:
    with patch("src.tools.loader.load_tools_from_dir", return_value=["my_tool"]):
        result = await tool.execute(name="my_tool", description="safe", code=CLEAN_TOOL)
    assert "approved" in result.lower()
    assert (tmp_path / "tools" / "my_tool.py").exists()
    assert not (tmp_path / "tools_pending" / "my_tool.py").exists()
    # Should be live immediately — no restart message
    assert "restart" not in result.lower()


@pytest.mark.asyncio
async def test_evil_tool_blocked_by_scanner(tmp_path: Path, approved_notifier: AsyncMock) -> None:
    tool = ProposeTool(tmp_path / "pending", tmp_path / "tools", approved_notifier)
    result = await tool.execute(name="evil_tool", description="bad", code=EVIL_TOOL)
    assert "BLOCKED" in result
    approved_notifier.send_diff.assert_not_awaited()


@pytest.mark.asyncio
async def test_denied_tool_not_moved(tmp_path: Path, denied_notifier: AsyncMock) -> None:
    tool = ProposeTool(tmp_path / "pending", tmp_path / "tools", denied_notifier)
    result = await tool.execute(name="my_tool", description="safe", code=CLEAN_TOOL)
    assert "rejected" in result.lower()
    assert not (tmp_path / "tools" / "my_tool.py").exists()


@pytest.mark.asyncio
async def test_invalid_name_rejected(tool: ProposeTool) -> None:
    result = await tool.execute(name="My-Tool!", description="x", code=CLEAN_TOOL)
    assert "invalid" in result.lower()


@pytest.mark.asyncio
async def test_notifier_receives_diff(tool: ProposeTool, approved_notifier: AsyncMock) -> None:
    await tool.execute(name="my_tool", description="safe", code=CLEAN_TOOL)
    approved_notifier.send_diff.assert_awaited_once()
    call_args = approved_notifier.send_diff.call_args
    assert call_args[0][0] == "my_tool"


@pytest.mark.asyncio
async def test_atomic_write_staging_file_content(tool: ProposeTool, tmp_path: Path) -> None:
    """Staged file must contain the exact code after write."""
    with patch("src.tools.loader.load_tools_from_dir", return_value=["my_tool"]):
        await tool.execute(name="my_tool", description="safe", code=CLEAN_TOOL)
    target = tmp_path / "tools" / "my_tool.py"
    assert target.read_text() == CLEAN_TOOL


@pytest.mark.asyncio
async def test_atomic_write_no_partial_on_error(tmp_path: Path, approved_notifier: AsyncMock) -> None:
    """If the write itself fails, no partial file should remain at the target path."""
    tool = ProposeTool(tmp_path / "pending", tmp_path / "tools", approved_notifier)

    # Make the target directory read-only so the atomic rename fails
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)

    # Patch NamedTemporaryFile.write to raise mid-write, simulating a crash
    import tempfile

    original_ntf = tempfile.NamedTemporaryFile

    def exploding_ntf(*args: object, **kwargs: object) -> object:
        m = original_ntf(*args, **kwargs)
        orig_write = m.write

        def bad_write(data: bytes) -> int:
            orig_write(data[:5])  # partial
            raise OSError("disk full")

        m.write = bad_write
        return m

    with (
        patch("src.tools.evolve.tempfile.NamedTemporaryFile", side_effect=exploding_ntf),
        patch("src.tools.loader.load_tools_from_dir", return_value=["my_tool"]),
    ):
        # Should raise or handle the error — either way, no partial file at target
        try:
            await tool.execute(name="my_tool", description="safe", code=CLEAN_TOOL)
        except OSError:
            pass

    target = tools_dir / "my_tool.py"
    # If the file exists, it must contain the full content, not partial
    if target.exists():
        assert target.read_text() == CLEAN_TOOL
