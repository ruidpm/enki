"""Tests for MemoryStore pattern support in build_context."""

from __future__ import annotations

from pathlib import Path

from src.memory.store import MemoryStore

# ---------------------------------------------------------------------------
# build_context includes patterns
# ---------------------------------------------------------------------------


def test_build_context_includes_patterns(tmp_path: Path) -> None:
    """When patterns.md exists, its content should appear in build_context output."""
    facts_path = tmp_path / "facts.md"
    facts_path.write_text("- User lives in Lisbon\n")
    patterns_path = tmp_path / "patterns.md"
    patterns_path.write_text("- User always checks git status before committing\n")

    s = MemoryStore(
        tmp_path / "mem.db",
        facts_path=facts_path,
        patterns_path=patterns_path,
    )
    s.append_turn("sess1", "user", "hello")
    ctx = s.build_context("hello", "sess1")
    assert "Behavioral patterns" in ctx
    assert "git status before committing" in ctx


# ---------------------------------------------------------------------------
# build_context graceful when patterns.md missing
# ---------------------------------------------------------------------------


def test_build_context_no_patterns_file(tmp_path: Path) -> None:
    """build_context should work fine when patterns.md does not exist."""
    facts_path = tmp_path / "facts.md"
    facts_path.write_text("- User lives in Lisbon\n")
    patterns_path = tmp_path / "nonexistent_patterns.md"

    s = MemoryStore(
        tmp_path / "mem.db",
        facts_path=facts_path,
        patterns_path=patterns_path,
    )
    s.append_turn("sess1", "user", "hello")
    ctx = s.build_context("hello", "sess1")
    # Should still contain facts, just no patterns section
    assert "User lives in Lisbon" in ctx
    assert "Behavioral patterns" not in ctx
