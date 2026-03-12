"""Tests for NotesTool — including H-03 max file size enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.notes import NotesTool


@pytest.fixture
def tool(tmp_path: Path) -> NotesTool:
    return NotesTool(notes_dir=tmp_path / "notes")


# ---------------------------------------------------------------------------
# Basic operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty(tool: NotesTool) -> None:
    result = await tool.execute(action="list")
    assert "No project notes" in result


@pytest.mark.asyncio
async def test_write_and_read(tool: NotesTool) -> None:
    await tool.execute(action="write", project="myproj", content="hello")
    result = await tool.execute(action="read", project="myproj")
    assert result == "hello"


@pytest.mark.asyncio
async def test_append(tool: NotesTool) -> None:
    await tool.execute(action="write", project="myproj", content="line1")
    await tool.execute(action="append", project="myproj", content="line2")
    result = await tool.execute(action="read", project="myproj")
    assert "line1" in result
    assert "line2" in result


# ---------------------------------------------------------------------------
# H-03: Max file size enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_rejects_oversized_content(tool: NotesTool) -> None:
    """Content larger than 1MB must be rejected on write."""
    big = "x" * 1_000_001
    result = await tool.execute(action="write", project="big", content=big)
    assert "too large" in result.lower() or "exceeds" in result.lower()
    # File should NOT have been created
    assert not (tool._dir / "big.md").exists()


@pytest.mark.asyncio
async def test_append_rejects_oversized_content(tool: NotesTool) -> None:
    """Content larger than 1MB must be rejected on append."""
    big = "x" * 1_000_001
    result = await tool.execute(action="append", project="big", content=big)
    assert "too large" in result.lower() or "exceeds" in result.lower()


@pytest.mark.asyncio
async def test_append_rejects_if_total_exceeds_2mb(tool: NotesTool) -> None:
    """Append that would push total file size over 2MB must be rejected."""
    # Directly create a 1.5MB file (bypassing the tool's 1MB write limit)
    path = tool._dir / "growbig.md"
    path.write_text("x" * 1_500_000)
    # Append another 600KB — total would be 2.1MB > 2MB limit
    extra = "y" * 600_000
    result = await tool.execute(action="append", project="growbig", content=extra)
    assert "too large" in result.lower() or "exceeds" in result.lower() or "exceed" in result.lower()


@pytest.mark.asyncio
async def test_write_accepts_content_under_limit(tool: NotesTool) -> None:
    """Content under 1MB must be accepted."""
    ok = "x" * 999_999
    result = await tool.execute(action="write", project="ok", content=ok)
    assert "saved" in result.lower()
