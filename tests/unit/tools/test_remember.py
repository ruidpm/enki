"""Unit tests for RememberTool and ForgetTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.remember import ForgetTool, RememberTool

# ---------------------------------------------------------------------------
# RememberTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remember_writes_fact(tmp_path: Path) -> None:
    facts = tmp_path / "facts.md"
    tool = RememberTool(facts_path=facts)

    result = await tool.execute(fact="User prefers dark mode")

    assert "Remembered" in result
    assert facts.read_text().strip() == "- User prefers dark mode"


@pytest.mark.asyncio
async def test_remember_duplicate_not_added(tmp_path: Path) -> None:
    facts = tmp_path / "facts.md"
    tool = RememberTool(facts_path=facts)

    await tool.execute(fact="User likes cats")
    result = await tool.execute(fact="User likes cats")

    assert "Already remembered" in result
    # Only one occurrence
    lines = [line for line in facts.read_text().splitlines() if line.strip()]
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_remember_duplicate_case_insensitive(tmp_path: Path) -> None:
    facts = tmp_path / "facts.md"
    tool = RememberTool(facts_path=facts)

    await tool.execute(fact="User prefers dark mode")
    result = await tool.execute(fact="user prefers DARK MODE")

    assert "Already remembered" in result


@pytest.mark.asyncio
async def test_remember_creates_file_when_missing(tmp_path: Path) -> None:
    # facts.md inside a subdirectory that doesn't exist yet
    facts = tmp_path / "sub" / "facts.md"
    tool = RememberTool(facts_path=facts)

    result = await tool.execute(fact="first fact")

    assert "Remembered" in result
    assert facts.exists()


@pytest.mark.asyncio
async def test_remember_strips_bullet_prefix(tmp_path: Path) -> None:
    facts = tmp_path / "facts.md"
    tool = RememberTool(facts_path=facts)

    await tool.execute(fact="- already bulleted")

    assert facts.read_text().strip() == "- already bulleted"


@pytest.mark.asyncio
async def test_remember_empty_fact(tmp_path: Path) -> None:
    facts = tmp_path / "facts.md"
    tool = RememberTool(facts_path=facts)

    result = await tool.execute(fact="")

    assert "empty" in result.lower()


# ---------------------------------------------------------------------------
# ForgetTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forget_removes_matching_line(tmp_path: Path) -> None:
    facts = tmp_path / "facts.md"
    facts.write_text("- User prefers dark mode\n- User likes cats\n")
    tool = ForgetTool(facts_path=facts)

    result = await tool.execute(fact="dark mode")

    assert "Forgot 1" in result
    remaining = facts.read_text()
    assert "dark mode" not in remaining
    assert "cats" in remaining


@pytest.mark.asyncio
async def test_forget_no_match(tmp_path: Path) -> None:
    facts = tmp_path / "facts.md"
    facts.write_text("- User likes cats\n")
    tool = ForgetTool(facts_path=facts)

    result = await tool.execute(fact="dark mode")

    assert "No matching fact" in result


@pytest.mark.asyncio
async def test_forget_file_not_exists(tmp_path: Path) -> None:
    facts = tmp_path / "facts.md"
    tool = ForgetTool(facts_path=facts)

    result = await tool.execute(fact="anything")

    assert "No facts stored" in result


@pytest.mark.asyncio
async def test_forget_removes_multiple_matches(tmp_path: Path) -> None:
    facts = tmp_path / "facts.md"
    facts.write_text("- dark mode on desktop\n- dark mode on mobile\n- cats are great\n")
    tool = ForgetTool(facts_path=facts)

    result = await tool.execute(fact="dark mode")

    assert "Forgot 2" in result
    remaining = facts.read_text()
    assert "dark mode" not in remaining
    assert "cats" in remaining


@pytest.mark.asyncio
async def test_forget_empty_query(tmp_path: Path) -> None:
    facts = tmp_path / "facts.md"
    facts.write_text("- something\n")
    tool = ForgetTool(facts_path=facts)

    result = await tool.execute(fact="")

    assert "empty" in result.lower()
