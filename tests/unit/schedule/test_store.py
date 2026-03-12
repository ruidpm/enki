"""Tests for ScheduleStore — SQLite-backed persistent cron job registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.schedule.store import ScheduleStore


@pytest.fixture
def store(tmp_path: Path) -> ScheduleStore:
    return ScheduleStore(tmp_path / "schedule.db")


def test_upsert_and_get(store: ScheduleStore) -> None:
    store.upsert("oil_check", "0 7 * * *", "Check oil prices")
    job = store.get("oil_check")
    assert job is not None
    assert job["job_id"] == "oil_check"
    assert job["cron"] == "0 7 * * *"
    assert job["prompt"] == "Check oil prices"
    assert job["enabled"] == 1
    assert job["run_count"] == 0
    assert job["last_run"] is None


def test_upsert_idempotent(store: ScheduleStore) -> None:
    store.upsert("j1", "0 8 * * *", "original prompt")
    store.upsert("j1", "0 9 * * *", "updated prompt")
    job = store.get("j1")
    assert job is not None
    assert job["cron"] == "0 9 * * *"
    assert job["prompt"] == "updated prompt"
    assert len(store.list_all()) == 1  # no duplicate


def test_get_returns_none_for_unknown(store: ScheduleStore) -> None:
    assert store.get("nonexistent") is None


def test_list_enabled_excludes_disabled(store: ScheduleStore) -> None:
    store.upsert("active", "0 8 * * *", "active job")
    store.upsert("paused", "0 9 * * *", "paused job", enabled=False)
    enabled = store.list_enabled()
    ids = [j["job_id"] for j in enabled]
    assert "active" in ids
    assert "paused" not in ids


def test_list_all_includes_all(store: ScheduleStore) -> None:
    store.upsert("a", "0 1 * * *", "a")
    store.upsert("b", "0 2 * * *", "b", enabled=False)
    all_jobs = store.list_all()
    ids = [j["job_id"] for j in all_jobs]
    assert "a" in ids
    assert "b" in ids


def test_set_enabled_disables(store: ScheduleStore) -> None:
    store.upsert("j", "0 8 * * *", "job")
    result = store.set_enabled("j", False)
    assert result is True
    job = store.get("j")
    assert job is not None
    assert job["enabled"] == 0


def test_set_enabled_returns_false_for_unknown(store: ScheduleStore) -> None:
    assert store.set_enabled("ghost", False) is False


def test_remove(store: ScheduleStore) -> None:
    store.upsert("del_me", "0 8 * * *", "gone")
    result = store.remove("del_me")
    assert result is True
    assert store.get("del_me") is None


def test_remove_returns_false_for_unknown(store: ScheduleStore) -> None:
    assert store.remove("ghost") is False


def test_record_run_increments_count_and_sets_last_run(store: ScheduleStore) -> None:
    store.upsert("j", "0 8 * * *", "job")
    store.record_run("j")
    store.record_run("j")
    job = store.get("j")
    assert job is not None
    assert job["run_count"] == 2
    assert job["last_run"] is not None
