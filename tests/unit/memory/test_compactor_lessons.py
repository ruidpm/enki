"""Tests for lesson extraction in MemoryCompactor."""

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


@pytest.fixture
def lessons_path(tmp_path: Path) -> Path:
    return tmp_path / "lessons.md"


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
# Lesson extraction from session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_lessons_from_session(
    store: MemoryStore, facts_path: Path, patterns_path: Path, lessons_path: Path
) -> None:
    """Compactor should extract lessons and write lessons.md."""
    store.append_turn("sess1", "user", "that approach failed, use sqlite3 CLI instead of Python SDK")
    store.append_turn("sess1", "assistant", "Got it, switching to sqlite3 CLI")

    # Call sequence (no followup callback, no existing facts/patterns):
    # 1. extract facts → returns fact (non-empty, proceeds)
    # 2. extract patterns → empty (no merge)
    # 3. extract lessons → returns lesson
    client = _make_client(
        "User prefers CLI tools",  # extract facts
        "",  # extract patterns (none)
        "sqlite3 CLI is more reliable than Python sqlite3 module for dump operations",  # extract lessons
    )
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
        lessons_path=lessons_path,
    )

    await compactor.compact_session("sess1")

    assert lessons_path.exists()
    content = lessons_path.read_text()
    assert "sqlite3 CLI" in content


# ---------------------------------------------------------------------------
# Merge lessons with existing — dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_lessons_dedup(store: MemoryStore, facts_path: Path, patterns_path: Path, lessons_path: Path) -> None:
    """When lessons.md already exists, compactor should merge via LLM."""
    lessons_path.parent.mkdir(parents=True, exist_ok=True)
    lessons_path.write_text("- type: ignore hacks mask real bugs\n")

    store.append_turn("sess2", "user", "that mock broke, patch _run_cmd instead")
    store.append_turn("sess2", "assistant", "Fixed")

    # Call sequence (no followup callback, lessons.md exists):
    # 1. extract facts → returns fact
    # 2. extract patterns → empty
    # 3. extract lessons → returns new lesson
    # 4. merge lessons (existing + new)
    client = _make_client(
        "User patches _run_cmd",  # extract facts
        "",  # extract patterns (none)
        "Patch _run_cmd instead of mocking subprocess directly",  # extract lessons
        "type: ignore hacks mask real bugs\nPatch _run_cmd instead of mocking subprocess directly",  # merge
    )
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
        lessons_path=lessons_path,
    )

    await compactor.compact_session("sess2")

    content = lessons_path.read_text()
    assert "_run_cmd" in content


# ---------------------------------------------------------------------------
# Lessons capped at 50
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lessons_capped_at_50(store: MemoryStore, facts_path: Path, patterns_path: Path, lessons_path: Path) -> None:
    """Lessons file should never exceed 50 entries."""
    store.append_turn("sess3", "user", "hello")
    store.append_turn("sess3", "assistant", "hi")

    many_lessons = "\n".join(f"Lesson number {i}" for i in range(60))
    # 1. extract facts, 2. extract patterns (empty), 3. extract lessons (60)
    client = _make_client(
        "Some fact",
        "",  # no patterns
        many_lessons,
    )
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
        lessons_path=lessons_path,
    )

    await compactor.compact_session("sess3")

    lines = [ln for ln in lessons_path.read_text().splitlines() if ln.strip()]
    assert len(lines) <= 50


# ---------------------------------------------------------------------------
# No lessons returns empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_lessons_skips_file(store: MemoryStore, facts_path: Path, patterns_path: Path, lessons_path: Path) -> None:
    """Empty extraction produces no lessons file."""
    # 1. extract facts, 2. extract patterns (empty), 3. extract lessons (empty)
    client = _make_client(
        "Some fact",
        "",  # no patterns
        "",  # no lessons
    )
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
        lessons_path=lessons_path,
    )

    store.append_turn("sess4", "user", "hi")
    store.append_turn("sess4", "assistant", "hello")

    await compactor.compact_session("sess4")

    assert not lessons_path.exists()


# ---------------------------------------------------------------------------
# clean_lessons
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_lessons(store: MemoryStore, facts_path: Path, patterns_path: Path, lessons_path: Path) -> None:
    """clean_lessons should prune/merge stale lessons and rewrite file."""
    lines = [f"- Lesson {i}" for i in range(25)]
    lessons_path.parent.mkdir(parents=True, exist_ok=True)
    lessons_path.write_text("\n".join(lines) + "\n")

    cleaned = "Cleaned lesson 1\nCleaned lesson 2"
    client = _make_client(cleaned)
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
        lessons_path=lessons_path,
    )

    result = await compactor.clean_lessons()
    assert result is True
    content = lessons_path.read_text()
    assert "Cleaned lesson 1" in content
