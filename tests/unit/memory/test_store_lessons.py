"""Tests for MemoryStore lesson support in build_context."""

from __future__ import annotations

from pathlib import Path

from src.memory.store import MemoryStore


def test_build_context_includes_lessons(tmp_path: Path) -> None:
    """When lessons.md exists, its content should appear in build_context output."""
    facts_path = tmp_path / "facts.md"
    facts_path.write_text("- User lives in Lisbon\n")
    patterns_path = tmp_path / "patterns.md"
    patterns_path.write_text("- User checks git status before committing\n")
    lessons_path = tmp_path / "lessons.md"
    lessons_path.write_text("- type: ignore hacks mask real production bugs\n")

    s = MemoryStore(
        tmp_path / "mem.db",
        facts_path=facts_path,
        patterns_path=patterns_path,
        lessons_path=lessons_path,
    )
    s.append_turn("sess1", "user", "hello")
    ctx = s.build_context("hello", "sess1")
    assert "Lessons learned" in ctx
    assert "type: ignore" in ctx


def test_build_context_no_lessons_file(tmp_path: Path) -> None:
    """build_context should work fine when lessons.md does not exist."""
    facts_path = tmp_path / "facts.md"
    facts_path.write_text("- User lives in Lisbon\n")
    lessons_path = tmp_path / "nonexistent_lessons.md"

    s = MemoryStore(
        tmp_path / "mem.db",
        facts_path=facts_path,
        lessons_path=lessons_path,
    )
    s.append_turn("sess1", "user", "hello")
    ctx = s.build_context("hello", "sess1")
    assert "User lives in Lisbon" in ctx
    assert "Lessons learned" not in ctx
