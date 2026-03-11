"""Tests for TasksTool — SQL injection prevention, CRUD operations."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.tasks import TasksTool


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "tasks.db"


@pytest.fixture
def tool(db_path: Path) -> TasksTool:
    return TasksTool(db_path)


def _mock_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    return proc


# ---------------------------------------------------------------------------
# SQL injection prevention — status filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_rejects_invalid_status(tool: TasksTool) -> None:
    """Status must be whitelisted; SQL injection via status is blocked."""
    result = await tool.execute(action="list", status="open'; DROP TABLE tasks;--")
    assert "invalid" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_list_accepts_valid_statuses(tool: TasksTool) -> None:
    """Only 'open' and 'done' are valid status values."""
    with patch("src.tools.tasks.asyncio.create_subprocess_exec", return_value=_mock_proc(stdout="[]")):
        result_open = await tool.execute(action="list", status="open")
    assert "invalid" not in result_open.lower()

    with patch("src.tools.tasks.asyncio.create_subprocess_exec", return_value=_mock_proc(stdout="[]")):
        result_done = await tool.execute(action="list", status="done")
    assert "invalid" not in result_done.lower()


# ---------------------------------------------------------------------------
# SQL injection prevention — create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_escapes_single_quotes_in_title(tool: TasksTool) -> None:
    """Single quotes in title must not break SQL."""
    captured_sql: list[str] = []
    original_proc = _mock_proc()

    async def capture_exec(*args: str, **kwargs: object) -> MagicMock:
        captured_sql.append(args[-1] if args else "")
        return original_proc

    with patch("src.tools.tasks.asyncio.create_subprocess_exec", side_effect=capture_exec):
        await tool.execute(action="create", title="O'Brien's task", notes="it's fine")

    # The SQL sent to sqlite3 must not have unescaped single quotes
    # that would break the statement
    sql_calls = [s for s in captured_sql if "INSERT" in s]
    assert len(sql_calls) == 1
    sql = sql_calls[0]
    # The title "O'Brien's task" must be safely escaped — quotes doubled
    assert "O''Brien" in sql or "O\\'Brien" in sql or "?" in sql


@pytest.mark.asyncio
async def test_create_with_empty_title_returns_error_or_succeeds(tool: TasksTool) -> None:
    """Empty title — implementation may reject or allow it."""
    with patch("src.tools.tasks.asyncio.create_subprocess_exec", return_value=_mock_proc()):
        result = await tool.execute(action="create", title="")
    # At minimum, no crash
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_create_injection_in_due_date(tool: TasksTool) -> None:
    """SQL injection via due_date must be blocked or safely handled."""
    captured_sql: list[str] = []

    async def capture_exec(*args: str, **kwargs: object) -> MagicMock:
        captured_sql.append(args[-1] if args else "")
        return _mock_proc()

    with patch("src.tools.tasks.asyncio.create_subprocess_exec", side_effect=capture_exec):
        await tool.execute(
            action="create",
            title="test",
            due_date="'); DROP TABLE tasks;--",
        )

    sql_calls = [s for s in captured_sql if "INSERT" in s]
    if sql_calls:
        sql = sql_calls[0]
        # The injection payload must be safely quoted
        assert "DROP TABLE" not in sql or ("''" in sql or "?" in sql)


# ---------------------------------------------------------------------------
# SQL injection prevention — update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_rejects_invalid_status(tool: TasksTool) -> None:
    """Status in update must be whitelisted."""
    result = await tool.execute(
        action="update", id=1, status="done'; DROP TABLE tasks;--"
    )
    assert "invalid" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_update_escapes_title_and_notes(tool: TasksTool) -> None:
    """Quotes in title/notes during update must be safely escaped."""
    captured_sql: list[str] = []

    async def capture_exec(*args: str, **kwargs: object) -> MagicMock:
        captured_sql.append(args[-1] if args else "")
        return _mock_proc()

    with patch("src.tools.tasks.asyncio.create_subprocess_exec", side_effect=capture_exec):
        await tool.execute(
            action="update",
            id=1,
            title="it's a test",
            notes="note with 'quotes'",
        )

    sql_calls = [s for s in captured_sql if "UPDATE" in s]
    assert len(sql_calls) == 1
    sql = sql_calls[0]
    assert "it''s" in sql or "?" in sql
    assert "'quotes''" in sql or "?" in sql


@pytest.mark.asyncio
async def test_update_validates_task_id_is_integer(tool: TasksTool) -> None:
    """task_id must be an integer, not injectable SQL."""
    result = await tool.execute(
        action="update", id="1; DROP TABLE tasks;--", status="done"  # type: ignore[arg-type]
    )
    assert "invalid" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# SQL injection prevention — delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_validates_task_id_is_integer(tool: TasksTool) -> None:
    """task_id for delete must be an integer."""
    result = await tool.execute(action="delete", id="1; DROP TABLE tasks;--")  # type: ignore[arg-type]
    assert "invalid" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# CRUD operations (basic correctness)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_returns_json(tool: TasksTool) -> None:
    rows = '[{"id":1,"title":"Buy milk","status":"open"}]'
    with patch("src.tools.tasks.asyncio.create_subprocess_exec", return_value=_mock_proc(stdout=rows)):
        result = await tool.execute(action="list")
    assert "Buy milk" in result


@pytest.mark.asyncio
async def test_list_empty_returns_empty_array(tool: TasksTool) -> None:
    with patch("src.tools.tasks.asyncio.create_subprocess_exec", return_value=_mock_proc(stdout="")):
        result = await tool.execute(action="list")
    assert result == "[]"


@pytest.mark.asyncio
async def test_create_returns_confirmation(tool: TasksTool) -> None:
    with patch("src.tools.tasks.asyncio.create_subprocess_exec", return_value=_mock_proc()):
        result = await tool.execute(action="create", title="Buy milk")
    assert "Buy milk" in result or "created" in result.lower()


@pytest.mark.asyncio
async def test_update_returns_confirmation(tool: TasksTool) -> None:
    with patch("src.tools.tasks.asyncio.create_subprocess_exec", return_value=_mock_proc()):
        result = await tool.execute(action="update", id=1, status="done")
    assert "updated" in result.lower()


@pytest.mark.asyncio
async def test_delete_returns_confirmation(tool: TasksTool) -> None:
    with patch("src.tools.tasks.asyncio.create_subprocess_exec", return_value=_mock_proc()):
        result = await tool.execute(action="delete", id=1)
    assert "deleted" in result.lower()


@pytest.mark.asyncio
async def test_unknown_action(tool: TasksTool) -> None:
    with patch("src.tools.tasks.asyncio.create_subprocess_exec", return_value=_mock_proc()):
        result = await tool.execute(action="explode")
    assert "unknown" in result.lower()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_due_date_validates_format(tool: TasksTool) -> None:
    """due_date with SQL injection payload must be handled safely."""
    captured_sql: list[str] = []

    async def capture_exec(*args: str, **kwargs: object) -> MagicMock:
        captured_sql.append(args[-1] if args else "")
        return _mock_proc()

    with patch("src.tools.tasks.asyncio.create_subprocess_exec", side_effect=capture_exec):
        await tool.execute(
            action="update",
            id=1,
            due_date="2025-01-01'); DROP TABLE tasks;--",
        )

    sql_calls = [s for s in captured_sql if "UPDATE" in s]
    if sql_calls:
        sql = sql_calls[0]
        # Injection must be escaped
        assert "DROP TABLE" not in sql or "''" in sql
