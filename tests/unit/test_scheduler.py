"""Tests for the scheduler."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.scheduler import Scheduler, ScheduledJob, default_jobs


@pytest.fixture
def mock_agent() -> MagicMock:
    agent = MagicMock()
    agent.run_turn = AsyncMock(return_value="Here's your morning briefing.")
    return agent


@pytest.fixture
def mock_notifier() -> MagicMock:
    notifier = MagicMock()
    notifier.send = AsyncMock()
    return notifier


@pytest.fixture
def scheduler(mock_agent: MagicMock, mock_notifier: MagicMock) -> Scheduler:
    return Scheduler(agent=mock_agent, notifier=mock_notifier)


def test_scheduler_instantiates(scheduler: Scheduler) -> None:
    assert scheduler is not None


def test_add_job_registers(scheduler: Scheduler) -> None:
    scheduler.add_job(ScheduledJob(
        job_id="morning_briefing",
        cron="0 8 * * *",
        prompt="Give me a morning briefing: tasks due today, calendar events.",
    ))
    assert "morning_briefing" in scheduler.jobs


def test_add_duplicate_job_overwrites(scheduler: Scheduler) -> None:
    job = ScheduledJob(job_id="test", cron="0 9 * * *", prompt="hello")
    scheduler.add_job(job)
    scheduler.add_job(ScheduledJob(job_id="test", cron="0 10 * * *", prompt="updated"))
    assert scheduler.jobs["test"].cron == "0 10 * * *"


@pytest.mark.asyncio
async def test_run_job_calls_agent_and_notifier(
    scheduler: Scheduler, mock_agent: MagicMock, mock_notifier: MagicMock
) -> None:
    job = ScheduledJob(job_id="test", cron="0 8 * * *", prompt="daily briefing")
    await scheduler._run_job(job)
    mock_agent.run_turn.assert_awaited_once_with("daily briefing")
    mock_notifier.send.assert_awaited_once_with("Here's your morning briefing.")


def test_default_jobs_returns_expected_ids() -> None:
    jobs = default_jobs()
    ids = {j.job_id for j in jobs}
    assert "morning_briefing" in ids
    assert "deadline_check" in ids


def test_disabled_job_not_added_to_apscheduler(scheduler: Scheduler) -> None:
    job = ScheduledJob(job_id="disabled", cron="0 8 * * *", prompt="x", enabled=False)
    scheduler.add_job(job)
    assert "disabled" in scheduler.jobs
    # APScheduler should not have this job since it's disabled
    with pytest.raises(Exception):
        scheduler._scheduler.get_job("disabled") or (_ for _ in ()).throw(KeyError("not found"))


@pytest.mark.asyncio
async def test_run_job_error_does_not_raise(
    scheduler: Scheduler, mock_agent: MagicMock
) -> None:
    mock_agent.run_turn = AsyncMock(side_effect=RuntimeError("boom"))
    job = ScheduledJob(job_id="test", cron="0 8 * * *", prompt="x")
    await scheduler._run_job(job)  # should not raise
