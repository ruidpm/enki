"""Tests for pattern extraction in MemoryCompactor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from anthropic.types import TextBlock

from src.memory.compactor import MemoryCompactor
from src.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "mem.db")


@pytest.fixture
def facts_path(tmp_path: Path) -> Path:
    return tmp_path / "facts.md"


@pytest.fixture
def patterns_path(tmp_path: Path) -> Path:
    return tmp_path / "patterns.md"


def _make_client(*responses: str) -> MagicMock:
    """Create a mock Anthropic client that returns the given texts in order."""
    client = MagicMock()
    msgs: list[MagicMock] = []
    for text in responses:
        msg = MagicMock()
        msg.content = [MagicMock(spec=TextBlock, text=text)]
        msgs.append(msg)
    client.messages = MagicMock()
    client.messages.create = AsyncMock(side_effect=msgs)
    return client


# ---------------------------------------------------------------------------
# Pattern extraction from session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_patterns_from_session(store: MemoryStore, facts_path: Path, patterns_path: Path) -> None:
    """Compactor should extract behavioral patterns and write patterns.md."""
    store.append_turn("sess1", "user", "git status")
    store.append_turn("sess1", "assistant", "All clean")
    store.append_turn("sess1", "user", "ok let's commit")

    # Response 1: fact extraction, Response 2: pattern extraction
    client = _make_client(
        "User uses git frequently",
        "User always checks git status before committing",
    )
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
    )

    await compactor.compact_session("sess1")

    assert patterns_path.exists()
    content = patterns_path.read_text()
    assert "git status before committing" in content


# ---------------------------------------------------------------------------
# Merge patterns with existing — dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_patterns_dedup(store: MemoryStore, facts_path: Path, patterns_path: Path) -> None:
    """When patterns.md already exists, compactor should merge (3rd LLM call)."""
    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    patterns_path.write_text("- User checks git status before committing\n")

    store.append_turn("sess2", "user", "let me check the diff first")
    store.append_turn("sess2", "assistant", "Here's the diff")

    # Response 1: fact extraction
    # Response 2: pattern extraction (new patterns)
    # Response 3: pattern merge (merge new + existing)
    client = _make_client(
        "User reviews code carefully",
        "User reviews diffs before committing",
        "User checks git status before committing\nUser reviews diffs before committing",
    )
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
    )

    await compactor.compact_session("sess2")

    # Should have called LLM 3 times: extract facts, extract patterns, merge patterns
    assert client.messages.create.await_count == 3
    content = patterns_path.read_text()
    assert "git status" in content


# ---------------------------------------------------------------------------
# Patterns capped at 50
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patterns_capped_at_50(store: MemoryStore, facts_path: Path, patterns_path: Path) -> None:
    """Patterns file should never exceed 50 entries."""
    store.append_turn("sess3", "user", "hello")
    store.append_turn("sess3", "assistant", "hi")

    # Generate 60 patterns from the LLM
    many_patterns = "\n".join(f"Pattern number {i}" for i in range(60))
    client = _make_client(
        "Some fact",
        many_patterns,
    )
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
    )

    await compactor.compact_session("sess3")

    lines = [ln for ln in patterns_path.read_text().splitlines() if ln.strip()]
    assert len(lines) <= 50


# ---------------------------------------------------------------------------
# No patterns returns empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_patterns_returns_empty(store: MemoryStore, facts_path: Path, patterns_path: Path) -> None:
    """Empty session produces no patterns file."""
    client = _make_client("Some fact", "")
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
    )

    store.append_turn("sess4", "user", "hi")
    store.append_turn("sess4", "assistant", "hello")

    await compactor.compact_session("sess4")

    # patterns.md should not be created if no patterns were extracted
    assert not patterns_path.exists()


# ---------------------------------------------------------------------------
# clean_patterns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_patterns(store: MemoryStore, facts_path: Path, patterns_path: Path) -> None:
    """clean_patterns should prune/merge stale patterns and rewrite file."""
    # Write enough patterns to trigger cleanup (>20 lines)
    lines = [f"- Pattern {i}" for i in range(25)]
    patterns_path.parent.mkdir(parents=True, exist_ok=True)
    patterns_path.write_text("\n".join(lines) + "\n")

    cleaned = "Cleaned pattern 1\nCleaned pattern 2"
    client = _make_client(cleaned)
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
    )

    result = await compactor.clean_patterns()
    assert result is True
    content = patterns_path.read_text()
    assert "Cleaned pattern 1" in content
