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
