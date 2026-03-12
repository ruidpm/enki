"""Tests for remember/forget tools — immediate fact persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.remember import ForgetTool, RememberTool


class TestRememberTool:
    """RememberTool should write facts to facts.md immediately."""

    @pytest.fixture
    def facts_path(self, tmp_path: Path) -> Path:
        return tmp_path / "facts.md"

    @pytest.fixture
    def tool(self, facts_path: Path) -> RememberTool:
        return RememberTool(facts_path=facts_path)

    def test_tool_metadata(self, tool: RememberTool) -> None:
        assert tool.name == "remember"
        assert "fact" in tool.input_schema["properties"]

    @pytest.mark.asyncio
    async def test_remember_creates_file(self, tool: RememberTool, facts_path: Path) -> None:
        result = await tool.execute(fact="User prefers dark mode")
        assert facts_path.exists()
        assert "User prefers dark mode" in facts_path.read_text()
        assert "Remembered" in result

    @pytest.mark.asyncio
    async def test_remember_appends_to_existing(self, tool: RememberTool, facts_path: Path) -> None:
        facts_path.write_text("- User lives in Porto\n")
        await tool.execute(fact="User prefers TDD")
        content = facts_path.read_text()
        assert "User lives in Porto" in content
        assert "User prefers TDD" in content

    @pytest.mark.asyncio
    async def test_remember_deduplicates(self, tool: RememberTool, facts_path: Path) -> None:
        facts_path.write_text("- User prefers dark mode\n")
        result = await tool.execute(fact="User prefers dark mode")
        assert "already" in result.lower()
        lines = [line for line in facts_path.read_text().splitlines() if line.strip()]
        assert len(lines) == 1

    @pytest.mark.asyncio
    async def test_remember_empty_fact_rejected(self, tool: RememberTool) -> None:
        result = await tool.execute(fact="")
        assert "empty" in result.lower() or "missing" in result.lower()

    @pytest.mark.asyncio
    async def test_remember_strips_bullet_prefix(self, tool: RememberTool, facts_path: Path) -> None:
        await tool.execute(fact="- User likes coffee")
        content = facts_path.read_text()
        # Should not double-prefix: "- - User likes coffee"
        assert "- User likes coffee" in content
        assert "- - " not in content


class TestForgetTool:
    """ForgetTool should remove matching facts from facts.md."""

    @pytest.fixture
    def facts_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "facts.md"
        p.write_text("- User lives in Porto\n- User prefers dark mode\n- User has a cat named Mochi\n")
        return p

    @pytest.fixture
    def tool(self, facts_path: Path) -> ForgetTool:
        return ForgetTool(facts_path=facts_path)

    def test_tool_metadata(self, tool: ForgetTool) -> None:
        assert tool.name == "forget"

    @pytest.mark.asyncio
    async def test_forget_removes_matching_fact(self, tool: ForgetTool, facts_path: Path) -> None:
        result = await tool.execute(fact="dark mode")
        content = facts_path.read_text()
        assert "dark mode" not in content
        assert "Porto" in content
        assert "Mochi" in content
        assert "Removed" in result or "Forgot" in result

    @pytest.mark.asyncio
    async def test_forget_no_match(self, tool: ForgetTool, facts_path: Path) -> None:
        result = await tool.execute(fact="nonexistent thing")
        assert "not found" in result.lower() or "no matching" in result.lower()

    @pytest.mark.asyncio
    async def test_forget_case_insensitive(self, tool: ForgetTool, facts_path: Path) -> None:
        await tool.execute(fact="DARK MODE")
        content = facts_path.read_text()
        assert "dark mode" not in content

    @pytest.mark.asyncio
    async def test_forget_empty_query_rejected(self, tool: ForgetTool) -> None:
        result = await tool.execute(fact="")
        assert "empty" in result.lower() or "missing" in result.lower()
