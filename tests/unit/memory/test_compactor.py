"""Tests for MemoryCompactor — merge/dedup flow."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.memory.compactor import MemoryCompactor
from src.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "mem.db")


@pytest.fixture
def facts_path(tmp_path: Path) -> Path:
    return tmp_path / "facts.md"


def _make_client(response_text: str) -> MagicMock:
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=msg)
    return client


@pytest.fixture
def compactor(store: MemoryStore, facts_path: Path) -> MemoryCompactor:
    client = _make_client("User prefers concise responses\nUser is house-hunting in Lisbon")
    return MemoryCompactor(store=store, anthropic_client=client, facts_path=facts_path)


# ---------------------------------------------------------------------------
# No turns — nothing happens
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_session_returns_empty(compactor: MemoryCompactor, facts_path: Path) -> None:
    facts = await compactor.compact_session("sess_empty")
    assert facts == []
    assert not facts_path.exists()


# ---------------------------------------------------------------------------
# Fresh facts.md (no existing file)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_creates_facts_md_when_new(
    compactor: MemoryCompactor, store: MemoryStore, facts_path: Path
) -> None:
    store.append_turn("sess1", "user", "I prefer short answers")
    store.append_turn("sess1", "assistant", "Got it")

    facts = await compactor.compact_session("sess1")
    assert len(facts) > 0
    assert facts_path.exists()
    content = facts_path.read_text()
    assert "User prefers concise responses" in content


# ---------------------------------------------------------------------------
# facts.md is REWRITTEN not appended
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rewrites_not_appends(
    store: MemoryStore, facts_path: Path
) -> None:
    # Pre-populate facts.md with existing fact
    facts_path.write_text("- User lives in Porto\n")

    # Compactor returns merged facts (simulating haiku merge)
    client = _make_client("User lives in Porto\nUser prefers concise responses")
    compactor = MemoryCompactor(store=store, anthropic_client=client, facts_path=facts_path)

    store.append_turn("sess2", "user", "I like short answers")
    store.append_turn("sess2", "assistant", "OK")

    await compactor.compact_session("sess2")
    content = facts_path.read_text()

    # Should not have duplicate session headers or growing sections
    assert content.count("User lives in Porto") == 1
    # Should be a clean list, not appended sections
    assert "##" not in content  # no session date headers


# ---------------------------------------------------------------------------
# Compactor calls haiku twice (extract then merge)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_llm_calls_when_existing_facts(
    store: MemoryStore, facts_path: Path
) -> None:
    facts_path.write_text("- User is house-hunting\n")

    client = _make_client("User is house-hunting\nUser prefers haiku")
    compactor = MemoryCompactor(store=store, anthropic_client=client, facts_path=facts_path)

    store.append_turn("sess3", "user", "I love short poems")
    store.append_turn("sess3", "assistant", "Nice!")

    await compactor.compact_session("sess3")
    # Should have called LLM twice: once to extract, once to merge
    assert client.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_one_llm_call_when_no_existing_facts(
    store: MemoryStore, facts_path: Path
) -> None:
    # No existing facts.md
    client = _make_client("User prefers concise")
    compactor = MemoryCompactor(store=store, anthropic_client=client, facts_path=facts_path)

    store.append_turn("sess4", "user", "hi")
    store.append_turn("sess4", "assistant", "hello")

    await compactor.compact_session("sess4")
    # Only one LLM call needed (no existing facts to merge)
    assert client.messages.create.await_count == 1


# ---------------------------------------------------------------------------
# facts.md format: one fact per line prefixed with "- "
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_facts_md_format(
    store: MemoryStore, facts_path: Path
) -> None:
    client = _make_client("User lives in Lisbon\nUser works on personal assistant project")
    compactor = MemoryCompactor(store=store, anthropic_client=client, facts_path=facts_path)

    store.append_turn("sess5", "user", "I work in Lisbon")

    await compactor.compact_session("sess5")
    lines = [l for l in facts_path.read_text().splitlines() if l.strip()]
    assert all(line.startswith("- ") for line in lines)
