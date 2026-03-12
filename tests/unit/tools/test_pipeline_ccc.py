"""Tests for PipelineCCCTool — synchronous CCC wrapper for pipeline sub-agents."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.pipeline_ccc import PipelineCCCTool


def _make_tool(
    workspace_path: str = "/tmp/test-workspace",
    language: str = "python",
) -> PipelineCCCTool:
    return PipelineCCCTool(
        workspace_path=workspace_path,
        language=language,
    )


def _patch_path_no_claude_md() -> patch:
    """Patch Path.exists to return False (no existing CLAUDE.md)."""
    return patch.object(Path, "exists", return_value=False)


def _patch_path_has_claude_md() -> patch:
    """Patch Path.exists to return True (existing CLAUDE.md — don't touch)."""
    return patch.object(Path, "exists", return_value=True)


class TestPipelineCCCTool:
    def test_name_and_schema(self) -> None:
        tool = _make_tool()
        assert tool.name == "run_code_task"
        assert "task" in tool.input_schema["properties"]
        assert "task" in tool.input_schema.get("required", [])

    @pytest.mark.asyncio
    async def test_returns_stdout_on_success(self) -> None:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"test output here", b""))

        tool = _make_tool()
        with (
            _patch_path_has_claude_md(),
            patch("src.tools.pipeline_ccc.asyncio") as mock_aio,
        ):
            mock_aio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_aio.subprocess = asyncio.subprocess
            mock_aio.wait_for = AsyncMock(return_value=(b"test output here", b""))
            result = await tool.execute(task="run pytest")

        assert "test output here" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_nonzero_exit(self) -> None:
        proc = AsyncMock()
        proc.returncode = 1

        tool = _make_tool()
        with (
            _patch_path_has_claude_md(),
            patch("src.tools.pipeline_ccc.asyncio") as mock_aio,
        ):
            mock_aio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_aio.subprocess = asyncio.subprocess
            mock_aio.wait_for = AsyncMock(return_value=(b"", b"some error"))
            result = await tool.execute(task="run pytest")

        assert "[ERROR]" in result

    @pytest.mark.asyncio
    async def test_returns_timeout_message(self) -> None:
        proc = AsyncMock()
        proc.kill = AsyncMock()
        proc.wait = AsyncMock()

        tool = _make_tool()
        with (
            _patch_path_has_claude_md(),
            patch("src.tools.pipeline_ccc.asyncio") as mock_aio,
        ):
            mock_aio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_aio.subprocess = asyncio.subprocess
            mock_aio.wait_for = AsyncMock(side_effect=TimeoutError)
            result = await tool.execute(task="run pytest")

        assert "TIMEOUT" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_spawn_failure(self) -> None:
        tool = _make_tool()
        with (
            _patch_path_has_claude_md(),
            patch("src.tools.pipeline_ccc.asyncio") as mock_aio,
        ):
            mock_aio.create_subprocess_exec = AsyncMock(
                side_effect=FileNotFoundError("claude not found"),
            )
            mock_aio.subprocess = asyncio.subprocess
            result = await tool.execute(task="run pytest")

        assert "[ERROR]" in result
        assert "claude not found" in result

    @pytest.mark.asyncio
    async def test_empty_task_returns_error(self) -> None:
        tool = _make_tool()
        result = await tool.execute(task="")
        assert "[ERROR]" in result

    @pytest.mark.asyncio
    async def test_injects_and_cleans_claude_md(self) -> None:
        """CLAUDE.md written before CCC, cleaned up after."""
        proc = AsyncMock()
        proc.returncode = 0

        tool = _make_tool(language="python")
        with (
            _patch_path_no_claude_md(),
            patch.object(Path, "write_text") as mock_write,
            patch.object(Path, "unlink") as mock_unlink,
            patch("src.tools.pipeline_ccc.asyncio") as mock_aio,
        ):
            mock_aio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_aio.subprocess = asyncio.subprocess
            mock_aio.wait_for = AsyncMock(return_value=(b"done", b""))

            # exists() returns False first (no CLAUDE.md), then True for cleanup check
            with patch.object(Path, "exists", side_effect=[False, True]):
                await tool.execute(task="do something")

            mock_write.assert_called_once()
            assert "Python" in mock_write.call_args[0][0]
            mock_unlink.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_claude_md_if_exists(self) -> None:
        """Don't touch existing CLAUDE.md."""
        proc = AsyncMock()
        proc.returncode = 0

        tool = _make_tool()
        with (
            _patch_path_has_claude_md(),
            patch.object(Path, "write_text") as mock_write,
            patch("src.tools.pipeline_ccc.asyncio") as mock_aio,
        ):
            mock_aio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_aio.subprocess = asyncio.subprocess
            mock_aio.wait_for = AsyncMock(return_value=(b"done", b""))
            await tool.execute(task="do something")

        mock_write.assert_not_called()
