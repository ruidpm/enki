"""Tests for follow-up extraction in MemoryCompactor."""

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
    """Create a mock Anthropic client returning given texts in order."""
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
# Follow-up extraction from session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_followups_from_session(store: MemoryStore, facts_path: Path, patterns_path: Path) -> None:
    """Compactor should extract follow-ups from session via LLM call when callback is set."""
    store.append_turn("sess1", "user", "I'm waiting on John for the API review")
    store.append_turn("sess1", "assistant", "Got it, I'll track that")

    # Response 1: fact extraction
    # Response 2: pattern extraction (empty = no patterns)
    # Response 3: follow-up extraction
    client = _make_client(
        "User works with John",
        "",
        "Waiting on John for API review|John",
    )
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
    )
    # Must set a callback to trigger follow-up extraction
    callback = AsyncMock()
    compactor.set_followup_callback(callback)

    await compactor.compact_session("sess1")

    # The follow-up extraction LLM call should have been made
    # (fact extract + pattern extract + followup extract = 3 calls minimum)
    assert client.messages.create.await_count >= 3
    # Callback should have been called
    assert callback.await_count >= 1


# ---------------------------------------------------------------------------
# Follow-up callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_followup_callback_called(store: MemoryStore, facts_path: Path, patterns_path: Path) -> None:
    """When a followup_callback is set, it should be called for each extracted follow-up."""
    store.append_turn("sess2", "user", "Waiting on Alice for contract")
    store.append_turn("sess2", "assistant", "Noted")

    callback = AsyncMock()

    # Response 1: fact extraction
    # Response 2: pattern extraction (empty)
    # Response 3: follow-up extraction — pipe-delimited: item|person
    client = _make_client(
        "User is working on a contract",
        "",
        "Waiting on Alice for contract|Alice\nNeed to follow up with Bob on invoices|Bob",
    )
    compactor = MemoryCompactor(
        store=store,
        anthropic_client=client,
        facts_path=facts_path,
        patterns_path=patterns_path,
    )
    compactor.set_followup_callback(callback)

    await compactor.compact_session("sess2")

    # Callback should have been called twice (one per follow-up line)
    assert callback.await_count == 2
    # First call: item="Waiting on Alice for contract", person="Alice"
    first_call_args = callback.call_args_list[0]
    assert "Alice" in first_call_args[1].get("person", "") or "Alice" in str(first_call_args)
