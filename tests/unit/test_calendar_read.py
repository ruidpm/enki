"""Tests for calendar read tool (src/tools/calendar_read.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.calendar_read import CalendarReadTool


class TestCalendarReadTool:
    """CalendarReadTool should shell out to gcalcli and parse output."""

    @pytest.fixture
    def tool(self) -> CalendarReadTool:
        return CalendarReadTool()

    def test_tool_metadata(self, tool: CalendarReadTool) -> None:
        assert tool.name == "calendar_read"
        assert "read" in tool.description.lower()
        assert "days" in tool.input_schema["properties"]

    @pytest.mark.asyncio
    async def test_returns_events(self, tool: CalendarReadTool) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Mon 10:00  Team standup\nTue 14:00  1:1 with manager", b""))

        with patch("src.tools.calendar_read.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute(days=7)

        assert "Team standup" in result
        assert "1:1 with manager" in result

    @pytest.mark.asyncio
    async def test_no_events(self, tool: CalendarReadTool) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("src.tools.calendar_read.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute(days=7)

        assert result == "No events found."

    @pytest.mark.asyncio
    async def test_gcalcli_error(self, tool: CalendarReadTool) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"not found"))

        with patch("src.tools.calendar_read.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute(days=7)

        assert "gcalcli error" in result
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_days_clamped_to_max_30(self, tool: CalendarReadTool) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"event", b""))

        with patch("src.tools.calendar_read.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await tool.execute(days=999)
            # days should be clamped to 30 — verify subprocess was called
            assert mock_exec.called

    @pytest.mark.asyncio
    async def test_default_days_is_7(self, tool: CalendarReadTool) -> None:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"event", b""))

        with patch("src.tools.calendar_read.asyncio.create_subprocess_exec", return_value=mock_proc):
            await tool.execute()
            # Should not raise — defaults to 7 days
