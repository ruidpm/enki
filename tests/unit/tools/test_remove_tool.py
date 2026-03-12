"""Tests for RemoveToolTool — soft-disables a proposed tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.remove_tool import RemoveToolTool


@pytest.fixture
def tools_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tools"
    d.mkdir()
    return d


@pytest.fixture
def disabled_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tools_disabled"
    d.mkdir()
    return d


@pytest.fixture
def registry() -> dict:
    return {}


@pytest.fixture
def tool(tools_dir: Path, disabled_dir: Path, registry: dict) -> RemoveToolTool:
    return RemoveToolTool(tools_dir=tools_dir, disabled_dir=disabled_dir, registry=registry)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_moves_file_to_disabled(tool: RemoveToolTool, tools_dir: Path, disabled_dir: Path, registry: dict) -> None:
    src = tools_dir / "my_tool.py"
    src.write_text("# my tool code")
    registry["my_tool"] = object()

    result = await tool.execute(tool_name="my_tool")

    assert "disabled" in result.lower() or "removed" in result.lower()
    assert not src.exists()
    assert (disabled_dir / "my_tool.py").exists()
    assert (disabled_dir / "my_tool.py").read_text() == "# my tool code"
    assert "my_tool" not in registry


@pytest.mark.asyncio
async def test_remove_unregisters_from_registry(tool: RemoveToolTool, tools_dir: Path, registry: dict) -> None:
    (tools_dir / "calc.py").write_text("# calc")
    registry["calc"] = object()

    await tool.execute(tool_name="calc")

    assert "calc" not in registry


@pytest.mark.asyncio
async def test_disabled_dir_created_if_missing(tools_dir: Path, registry: dict, tmp_path: Path) -> None:
    disabled_dir = tmp_path / "tools_disabled"
    assert not disabled_dir.exists()

    t = RemoveToolTool(tools_dir=tools_dir, disabled_dir=disabled_dir, registry=registry)
    (tools_dir / "x.py").write_text("# x")
    registry["x"] = object()

    await t.execute(tool_name="x")

    assert disabled_dir.exists()
    assert (disabled_dir / "x.py").exists()


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocks_immutable_core_tool(tool: RemoveToolTool, registry: dict) -> None:
    registry["propose_tool"] = object()
    result = await tool.execute(tool_name="propose_tool")
    assert "immutable" in result.lower() or "error" in result.lower()
    assert "propose_tool" in registry


@pytest.mark.asyncio
async def test_error_if_not_registered(tool: RemoveToolTool) -> None:
    result = await tool.execute(tool_name="ghost_tool")
    assert "not found" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_error_if_no_file_found(tool: RemoveToolTool, registry: dict) -> None:
    # Registered but no .py file (e.g. a built-in registered via DI)
    registry["built_in"] = object()
    result = await tool.execute(tool_name="built_in")
    assert "not found" in result.lower() or "error" in result.lower()
    # Registry entry should remain (we didn't move anything)
    assert "built_in" in registry


@pytest.mark.asyncio
async def test_error_if_no_tool_name(tool: RemoveToolTool) -> None:
    result = await tool.execute(tool_name="")
    assert "error" in result.lower()
