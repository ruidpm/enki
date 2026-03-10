"""Tests for TeamsStore — SQLite-backed persistent team registry."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.teams.store import TeamsStore


@pytest.fixture
def store(tmp_path: Path) -> TeamsStore:
    return TeamsStore(tmp_path / "teams.db")


def test_create_and_get_team(store: TeamsStore) -> None:
    store.create_team(
        team_id="researcher",
        name="Research Team",
        role="You are a research specialist.",
        tools=["web_search"],
        monthly_token_budget=50_000,
    )
    team = store.get_team("researcher")
    assert team is not None
    assert team["team_id"] == "researcher"
    assert team["name"] == "Research Team"
    assert team["role"] == "You are a research specialist."
    assert team["tools"] == ["web_search"]
    assert team["monthly_token_budget"] == 50_000
    assert team["active"] == 1


def test_get_team_returns_none_for_unknown(store: TeamsStore) -> None:
    assert store.get_team("nonexistent") is None


def test_list_teams_returns_only_active(store: TeamsStore) -> None:
    store.create_team("a", "A", "role a", ["web_search"])
    store.create_team("b", "B", "role b", ["notes"])
    store.deactivate_team("b")

    teams = store.list_teams()
    ids = [t["team_id"] for t in teams]
    assert "a" in ids
    assert "b" not in ids


def test_deactivate_team(store: TeamsStore) -> None:
    store.create_team("x", "X", "role x", [])
    store.deactivate_team("x")
    team = store.get_team("x")
    assert team is not None
    assert team["active"] == 0


def test_log_task_and_stats(store: TeamsStore) -> None:
    store.create_team("t1", "T1", "role", ["web_search"])
    store.log_task("t1", "search news", "found stuff", tokens_used=100, success=True, duration_s=1.5)
    store.log_task("t1", "search more", "found more", tokens_used=200, success=True, duration_s=2.0)
    store.log_task("t1", "bad task", "error", tokens_used=50, success=False, duration_s=0.5)

    stats = store.team_stats("t1")
    assert stats["tasks_total"] == 3
    assert stats["tasks_success"] == 2
    assert abs(stats["success_rate"] - 2 / 3) < 0.01
    assert stats["tokens_month"] == 350


def test_monthly_tokens_scoped_to_current_month(store: TeamsStore) -> None:
    store.create_team("t2", "T2", "role", [])
    store.log_task("t2", "task", "ok", tokens_used=500, success=True, duration_s=1.0)
    used = store.monthly_tokens_used("t2")
    assert used == 500


def test_all_team_stats_returns_list(store: TeamsStore) -> None:
    store.create_team("alpha", "Alpha", "role", ["web_search"])
    store.create_team("beta", "Beta", "role", ["notes"])
    store.log_task("alpha", "task", "done", tokens_used=100, success=True, duration_s=1.0)

    all_stats = store.all_team_stats()
    assert len(all_stats) == 2
    ids = [s["team_id"] for s in all_stats]
    assert "alpha" in ids
    assert "beta" in ids


def test_budget_remaining_in_stats(store: TeamsStore) -> None:
    store.create_team("c", "C", "role", [], monthly_token_budget=1000)
    store.log_task("c", "task", "ok", tokens_used=300, success=True, duration_s=1.0)
    stats = store.team_stats("c")
    assert stats["budget_remaining"] == 700
