"""Tests for FollowUpsTool — CRUD operations via sqlite3 CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.follow_ups import FollowUpsTool


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "follow_ups.db"


@pytest.fixture
def tool(db_path: Path) -> FollowUpsTool:
    return FollowUpsTool(db_path)


def _mock_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    return proc


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_follow_up(tool: FollowUpsTool) -> None:
    """Creating a follow-up should return a success message."""
    with patch(
        "src.tools.follow_ups.asyncio.create_subprocess_exec",
        return_value=_mock_proc(),
    ):
        result = await tool.execute(action="create", item="Waiting on John for review")
    assert "created" in result.lower() or "follow" in result.lower()


@pytest.mark.asyncio
async def test_create_with_person_and_due_date(tool: FollowUpsTool) -> None:
    """All fields should be populated when provided."""
    captured_sql: list[str] = []

    async def capture_exec(*args: str, **kwargs: object) -> MagicMock:
        captured_sql.append(args[-1] if args else "")
        return _mock_proc()

    with patch(
        "src.tools.follow_ups.asyncio.create_subprocess_exec",
        side_effect=capture_exec,
    ):
        result = await tool.execute(
            action="create",
            item="Waiting on contract review",
            person="Alice",
            due_date="2026-03-20",
        )

    assert "created" in result.lower() or "follow" in result.lower()
    # Should have INSERT with person and due_date
    sql_calls = [s for s in captured_sql if "INSERT" in s]
    assert len(sql_calls) >= 1
    sql = sql_calls[0]
    assert "Alice" in sql
    assert "2026-03-20" in sql


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_open_follow_ups(tool: FollowUpsTool) -> None:
    """Default list should show only open items."""
    rows = '[{"id":1,"item":"Waiting on X","person":"","status":"open","due_date":"","created_at":"2026-03-10"}]'
    with patch(
        "src.tools.follow_ups.asyncio.create_subprocess_exec",
        return_value=_mock_proc(stdout=rows),
    ):
        result = await tool.execute(action="list")
    assert "Waiting on X" in result


@pytest.mark.asyncio
async def test_list_all_follow_ups(tool: FollowUpsTool) -> None:
    """list with status=all should return all items including closed."""
    rows = '[{"id":1,"item":"Done","status":"closed"},{"id":2,"item":"Open","status":"open"}]'
    with patch(
        "src.tools.follow_ups.asyncio.create_subprocess_exec",
        return_value=_mock_proc(stdout=rows),
    ):
        result = await tool.execute(action="list", status="all")
    assert "Done" in result
    assert "Open" in result


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_follow_up(tool: FollowUpsTool) -> None:
    """Closing a follow-up should return success message."""
    with patch(
        "src.tools.follow_ups.asyncio.create_subprocess_exec",
        return_value=_mock_proc(),
    ):
        result = await tool.execute(action="close", id=1)
    assert "closed" in result.lower()


@pytest.mark.asyncio
async def test_close_nonexistent_id(tool: FollowUpsTool) -> None:
    """Closing a non-existent ID should handle gracefully."""
    with patch(
        "src.tools.follow_ups.asyncio.create_subprocess_exec",
        return_value=_mock_proc(),
    ):
        result = await tool.execute(action="close", id=99999)
    # Should still return a string (either error or confirmation)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_action(tool: FollowUpsTool) -> None:
    """Unknown action should return an error message."""
    with patch(
        "src.tools.follow_ups.asyncio.create_subprocess_exec",
        return_value=_mock_proc(),
    ):
        result = await tool.execute(action="explode")
    assert "unknown" in result.lower()
