"""Tests for MemoryStore."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "test_memory.db")


def test_append_and_retrieve(store: MemoryStore) -> None:
    store.append_turn("sess1", "user", "hello world")
    turns = store.get_recent_turns("sess1")
    assert len(turns) == 1
    assert turns[0]["content"] == "hello world"
    assert turns[0]["role"] == "user"


def test_search_fts_basic(store: MemoryStore) -> None:
    store.append_turn("sess1", "user", "mortgage rates are rising")
    store.append_turn("sess1", "assistant", "yes rates have gone up")
    results = store.search_fts("mortgage")
    assert any("mortgage" in r["content"] for r in results)


def test_search_fts_punctuation_does_not_crash(store: MemoryStore) -> None:
    """Raw user messages with punctuation must not cause FTS5 syntax errors."""
    store.append_turn("sess1", "user", "hello who are you?")
    # These should all return without raising
    results = store.search_fts("hello who are you?")
    assert isinstance(results, list)


def test_search_fts_special_chars(store: MemoryStore) -> None:
    """Various FTS5 special characters must not crash search."""
    store.append_turn("sess1", "user", "test content")
    for query in ["hello?", "what's up!", "foo:bar", "test AND OR NOT", "*wildcard*", ""]:
        results = store.search_fts(query)
        assert isinstance(results, list)


def test_sanitize_fts_query_strips_specials() -> None:
    assert MemoryStore._sanitize_fts_query("hello?") == "hello"
    assert MemoryStore._sanitize_fts_query("foo:bar") == "foo bar"
    assert MemoryStore._sanitize_fts_query("what's up!") == "what s up"
    assert MemoryStore._sanitize_fts_query("") == ""
    assert MemoryStore._sanitize_fts_query("???") == ""


def test_add_and_get_facts(store: MemoryStore) -> None:
    store.add_fact("User prefers concise responses")
    facts = store.get_facts()
    assert "User prefers concise responses" in facts


def test_build_context_with_punctuated_query(store: MemoryStore) -> None:
    """build_context must not raise when user message contains punctuation."""
    store.append_turn("sess1", "user", "I need help with my mortgage")
    ctx = store.build_context("hello who are you?", "sess1")
    assert isinstance(ctx, str)


def test_get_recent_turns_order(store: MemoryStore) -> None:
    store.append_turn("sess1", "user", "first")
    store.append_turn("sess1", "assistant", "second")
    store.append_turn("sess1", "user", "third")
    turns = store.get_recent_turns("sess1")
    assert [t["content"] for t in turns] == ["first", "second", "third"]


def test_get_recent_turns_isolated_by_session(store: MemoryStore) -> None:
    store.append_turn("sess1", "user", "session one")
    store.append_turn("sess2", "user", "session two")
    sess1 = store.get_recent_turns("sess1")
    assert all(t["content"] == "session one" for t in sess1)


# ---------------------------------------------------------------------------
# Daily log tests
# ---------------------------------------------------------------------------

def test_append_turn_writes_daily_log(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    s = MemoryStore(tmp_path / "mem.db", logs_dir=logs_dir)
    s.append_turn("sess1", "user", "hello from daily log")
    from datetime import date
    log_file = logs_dir / f"{date.today().isoformat()}.md"
    assert log_file.exists()
    content = log_file.read_text()
    assert "hello from daily log" in content
    assert "USER" in content


def test_no_daily_log_without_logs_dir(tmp_path: Path) -> None:
    s = MemoryStore(tmp_path / "mem.db")  # no logs_dir
    s.append_turn("sess1", "user", "hello")
    # No logs directory should have been created
    assert not (tmp_path / "logs").exists()


def test_get_today_log_tail_returns_recent_lines(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    s = MemoryStore(tmp_path / "mem.db", logs_dir=logs_dir)
    for i in range(10):
        s.append_turn("sess1", "user", f"message {i}")
    tail = s.get_today_log_tail(n=5)
    assert "message 9" in tail
    # Should not include message 0..4 when tail=5
    tail.strip().splitlines()
    # Each turn writes 2 lines (content line + blank) — so 5 turns = up to 10 lines
    # Just verify we got the last messages
    assert "message 9" in tail


def test_get_today_log_tail_no_logs_dir(tmp_path: Path) -> None:
    s = MemoryStore(tmp_path / "mem.db")
    assert s.get_today_log_tail() == ""


def test_get_today_log_tail_no_file_yet(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    s = MemoryStore(tmp_path / "mem.db", logs_dir=logs_dir)
    assert s.get_today_log_tail() == ""


# ---------------------------------------------------------------------------
# build_context with facts_path
# ---------------------------------------------------------------------------

def test_build_context_reads_facts_md(tmp_path: Path) -> None:
    facts_path = tmp_path / "facts.md"
    facts_path.write_text("- User lives in Lisbon\n- User prefers concise answers\n")
    s = MemoryStore(tmp_path / "mem.db", facts_path=facts_path)
    s.append_turn("sess1", "user", "hello")
    ctx = s.build_context("hello", "sess1")
    assert "User lives in Lisbon" in ctx
    assert "User prefers concise answers" in ctx


def test_build_context_includes_today_log(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    s = MemoryStore(tmp_path / "mem.db", logs_dir=logs_dir)
    s.append_turn("sess1", "user", "what is the weather")
    ctx = s.build_context("weather", "sess1")
    assert "what is the weather" in ctx


def test_build_context_skips_fts_when_facts_path_set(tmp_path: Path) -> None:
    """When facts_path is configured, FTS results should not be included."""
    facts_path = tmp_path / "facts.md"
    facts_path.write_text("- User fact\n")
    s = MemoryStore(tmp_path / "mem.db", facts_path=facts_path)
    s.append_turn("sess1", "user", "mortgage rates are rising")
    ctx = s.build_context("mortgage", "sess1")
    # FTS section header should not appear when facts_path is set
    assert "Relevant past context" not in ctx
