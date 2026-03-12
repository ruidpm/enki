"""Tests for M-11: Memory compactor must use asyncio.to_thread for blocking file I/O."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_client(response_text: str) -> MagicMock:
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(spec=TextBlock, text=response_text)]
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=msg)
    return client


@pytest.mark.asyncio
async def test_compact_session_uses_to_thread_for_file_write(store: MemoryStore, facts_path: Path) -> None:
    """File write in compact_session must go through asyncio.to_thread."""
    client = _make_client("User prefers concise responses")
    compactor = MemoryCompactor(store=store, anthropic_client=client, facts_path=facts_path)
    store.append_turn("sess1", "user", "I prefer short answers")

    with patch("src.memory.compactor.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        # Make to_thread actually execute the callable so the test completes
        async def _run_sync(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            return fn(*args, **kwargs)

        mock_to_thread.side_effect = _run_sync

        facts = await compactor.compact_session("sess1")

    assert mock_to_thread.called
    assert len(facts) > 0


@pytest.mark.asyncio
async def test_clean_facts_uses_to_thread_for_file_write(store: MemoryStore, facts_path: Path) -> None:
    """File write in clean_facts must go through asyncio.to_thread."""
    # Write enough facts to trigger cleanup
    facts_path.write_text("\n".join(f"- Fact {i}" for i in range(35)))

    client = _make_client("\n".join(f"Cleaned fact {i}" for i in range(30)))
    compactor = MemoryCompactor(store=store, anthropic_client=client, facts_path=facts_path)

    with patch("src.memory.compactor.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:

        async def _run_sync(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            return fn(*args, **kwargs)

        mock_to_thread.side_effect = _run_sync

        result = await compactor.clean_facts()

    assert result is True
    assert mock_to_thread.called
