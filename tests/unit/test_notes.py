"""Tests for notes tool (src/tools/notes.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.notes import NotesTool


class TestNotesTool:
    """NotesTool should manage markdown files in a directory."""

    @pytest.fixture
    def tool(self, tmp_path: Path) -> NotesTool:
        return NotesTool(tmp_path / "notes")

    @pytest.fixture
    def notes_dir(self, tool: NotesTool) -> Path:
        return tool._dir

    def test_tool_metadata(self, tool: NotesTool) -> None:
        assert tool.name == "notes"
        assert "action" in tool.input_schema["properties"]

    @pytest.mark.asyncio
    async def test_list_empty(self, tool: NotesTool) -> None:
        result = await tool.execute(action="list")
        assert result == "No project notes yet."

    @pytest.mark.asyncio
    async def test_write_and_read(self, tool: NotesTool) -> None:
        await tool.execute(action="write", project="myproject", content="# Hello\nWorld")
        result = await tool.execute(action="read", project="myproject")
        assert "# Hello" in result
        assert "World" in result

    @pytest.mark.asyncio
    async def test_list_after_write(self, tool: NotesTool) -> None:
        await tool.execute(action="write", project="alpha", content="a")
        await tool.execute(action="write", project="beta", content="b")
        result = await tool.execute(action="list")
        assert "alpha" in result
        assert "beta" in result

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, tool: NotesTool) -> None:
        result = await tool.execute(action="read", project="nope")
        assert "No notes" in result

    @pytest.mark.asyncio
    async def test_append(self, tool: NotesTool) -> None:
        await tool.execute(action="write", project="proj", content="line1")
        await tool.execute(action="append", project="proj", content="line2")
        result = await tool.execute(action="read", project="proj")
        assert "line1" in result
        assert "line2" in result

    @pytest.mark.asyncio
    async def test_write_overwrites(self, tool: NotesTool) -> None:
        await tool.execute(action="write", project="proj", content="old")
        await tool.execute(action="write", project="proj", content="new")
        result = await tool.execute(action="read", project="proj")
        assert result == "new"

    @pytest.mark.asyncio
    async def test_missing_project_param(self, tool: NotesTool) -> None:
        result = await tool.execute(action="read")
        assert "Missing" in result

    @pytest.mark.asyncio
    async def test_invalid_project_name_raises(self, tool: NotesTool) -> None:
        with pytest.raises(ValueError, match="Invalid project name"):
            await tool.execute(action="read", project="../../etc/passwd")

    @pytest.mark.asyncio
    async def test_safe_name_rejects_dots(self, tool: NotesTool) -> None:
        with pytest.raises(ValueError):
            await tool.execute(action="write", project="foo.bar", content="x")

    @pytest.mark.asyncio
    async def test_safe_name_allows_hyphens_underscores(self, tool: NotesTool) -> None:
        result = await tool.execute(action="write", project="my-project_v2", content="ok")
        assert "saved" in result.lower()

    @pytest.mark.asyncio
    async def test_unknown_action(self, tool: NotesTool) -> None:
        result = await tool.execute(action="delete", project="test")
        assert "Unknown action" in result
