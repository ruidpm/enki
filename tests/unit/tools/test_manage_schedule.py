"""Tests for ListScheduleTool and ManageScheduleTool."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.schedule.store import ScheduleStore
from src.tools.manage_schedule import ListScheduleTool, ManageScheduleTool


@pytest.fixture
def store(tmp_path: Path) -> ScheduleStore:
    return ScheduleStore(tmp_path / "schedule.db")


@pytest.fixture
def scheduler() -> MagicMock:
    s = MagicMock()
    s.add_job = MagicMock()
    s.remove_job = MagicMock()
    s.set_job_enabled = MagicMock()
    s.jobs = {}
    return s


@pytest.fixture
def list_tool(store: ScheduleStore) -> ListScheduleTool:
    return ListScheduleTool(store=store)


@pytest.fixture
def manage_tool(store: ScheduleStore, scheduler: MagicMock) -> ManageScheduleTool:
    t = ManageScheduleTool(store=store)
    t.set_scheduler(scheduler)
    return t


# ---------------------------------------------------------------------------
# ListScheduleTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_empty_store(list_tool: ListScheduleTool) -> None:
    result = await list_tool.execute()
    assert "no" in result.lower() or result.strip() != ""


@pytest.mark.asyncio
async def test_list_shows_all_jobs(list_tool: ListScheduleTool, store: ScheduleStore) -> None:
    store.upsert("oil", "0 7 * * *", "Check oil prices")
    store.upsert("news", "0 8 * * *", "Check news", enabled=False)
    result = await list_tool.execute()
    assert "oil" in result
    assert "news" in result
    assert "0 7 * * *" in result


# ---------------------------------------------------------------------------
# ManageScheduleTool — add
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_persists_and_calls_scheduler(
    manage_tool: ManageScheduleTool, store: ScheduleStore, scheduler: MagicMock
) -> None:
    result = await manage_tool.execute(
        action="add",
        job_id="oil_daily",
        cron="0 7 * * *",
        prompt="Check oil prices",
    )
    assert "added" in result.lower() or "scheduled" in result.lower()
    assert store.get("oil_daily") is not None
    scheduler.add_job.assert_called_once()


@pytest.mark.asyncio
async def test_add_rejects_invalid_cron(manage_tool: ManageScheduleTool) -> None:
    result = await manage_tool.execute(
        action="add",
        job_id="bad",
        cron="not-a-cron",
        prompt="whatever",
    )
    assert "invalid" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_add_rejects_too_few_fields(manage_tool: ManageScheduleTool) -> None:
    result = await manage_tool.execute(
        action="add", job_id="bad", cron="0 8 * *", prompt="p"
    )
    assert "invalid" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# ManageScheduleTool — pause / resume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pause_disables_and_unschedules(
    manage_tool: ManageScheduleTool, store: ScheduleStore, scheduler: MagicMock
) -> None:
    store.upsert("j1", "0 8 * * *", "prompt")
    scheduler.jobs = {"j1": MagicMock()}
    result = await manage_tool.execute(action="pause", job_id="j1")
    assert "paused" in result.lower()
    job = store.get("j1")
    assert job is not None
    assert job["enabled"] == 0
    scheduler.set_job_enabled.assert_called_once_with("j1", False)


@pytest.mark.asyncio
async def test_resume_enables_and_reschedules(
    manage_tool: ManageScheduleTool, store: ScheduleStore, scheduler: MagicMock
) -> None:
    store.upsert("j2", "0 9 * * *", "prompt", enabled=False)
    result = await manage_tool.execute(action="resume", job_id="j2")
    assert "resumed" in result.lower()
    job = store.get("j2")
    assert job is not None
    assert job["enabled"] == 1
    scheduler.set_job_enabled.assert_called_once_with("j2", True)


# ---------------------------------------------------------------------------
# ManageScheduleTool — remove
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_deletes(
    manage_tool: ManageScheduleTool, store: ScheduleStore, scheduler: MagicMock
) -> None:
    store.upsert("bye", "0 8 * * *", "prompt")
    result = await manage_tool.execute(action="remove", job_id="bye")
    assert "removed" in result.lower()
    assert store.get("bye") is None
    scheduler.remove_job.assert_called_once_with("bye")


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_job_returns_error(manage_tool: ManageScheduleTool) -> None:
    for action in ("pause", "resume", "remove"):
        result = await manage_tool.execute(action=action, job_id="ghost")
        assert "not found" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_no_scheduler_wired_still_persists_to_store(store: ScheduleStore) -> None:
    tool = ManageScheduleTool(store=store)  # no set_scheduler() called
    result = await tool.execute(action="add", job_id="x", cron="0 8 * * *", prompt="p")
    # Should succeed — store updated, note about restart added
    assert "error" not in result.lower()
    assert store.get("x") is not None


@pytest.mark.asyncio
async def test_unknown_action_returns_error(manage_tool: ManageScheduleTool) -> None:
    result = await manage_tool.execute(action="nuke", job_id="x")
    assert "unknown" in result.lower() or "error" in result.lower()
