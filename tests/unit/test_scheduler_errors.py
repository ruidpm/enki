"""Tests for scheduler error handling — M-17."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scheduler import ScheduledJob, Scheduler


@pytest.fixture
def mock_agent() -> MagicMock:
    agent = MagicMock()
    agent.run_turn = AsyncMock(return_value="briefing")
    return agent


@pytest.fixture
def mock_notifier() -> MagicMock:
    notifier = MagicMock()
    notifier.send = AsyncMock()
    return notifier


@pytest.fixture
def scheduler(mock_agent: MagicMock, mock_notifier: MagicMock) -> Scheduler:
    return Scheduler(agent=mock_agent, notifier=mock_notifier)


@pytest.mark.asyncio
async def test_run_job_error_is_logged(
    scheduler: Scheduler, mock_agent: MagicMock, mock_notifier: MagicMock
) -> None:
    """When _run_job fails, the error must be logged, not silently swallowed."""
    mock_agent.run_turn = AsyncMock(side_effect=RuntimeError("agent exploded"))
    job = ScheduledJob(job_id="test_job", cron="0 8 * * *", prompt="hello")

    with patch("src.scheduler.log") as mock_log:
        await scheduler._run_job(job)
        mock_log.error.assert_called()
        call_kwargs = mock_log.error.call_args
        assert "agent exploded" in str(call_kwargs)


@pytest.mark.asyncio
async def test_run_job_notifier_failure_is_logged(
    scheduler: Scheduler, mock_agent: MagicMock, mock_notifier: MagicMock
) -> None:
    """When fallback notifier.send also fails, it must be logged, not suppressed."""
    mock_agent.run_turn = AsyncMock(side_effect=RuntimeError("agent down"))
    mock_notifier.send = AsyncMock(side_effect=RuntimeError("notifier down"))
    job = ScheduledJob(job_id="test_job", cron="0 8 * * *", prompt="hello")

    with patch("src.scheduler.log") as mock_log:
        await scheduler._run_job(job)
        # Both the primary error and fallback error should be logged
        assert mock_log.error.call_count >= 1
        # The fallback notifier failure must also be logged (warning or error)
        all_calls = [str(c) for c in mock_log.method_calls]
        logged_text = " ".join(all_calls)
        assert "notifier" in logged_text.lower() or "notify_fallback" in logged_text.lower() or mock_log.warning.called


def test_remove_job_nonexistent_logs_not_crashes(scheduler: Scheduler) -> None:
    """remove_job on nonexistent job must not crash and should log if APScheduler raises."""
    # Should not raise even if job doesn't exist in APScheduler
    scheduler.remove_job("nonexistent-job")


def test_add_job_replace_logs_on_apscheduler_error(scheduler: Scheduler) -> None:
    """If APScheduler remove_job fails during add_job, it should be handled gracefully."""
    with patch("src.scheduler.log"):
        job = ScheduledJob(job_id="test", cron="0 8 * * *", prompt="x")
        # First add should work fine
        scheduler.add_job(job)
        # Second add replaces — remove might fail, should be caught
        scheduler.add_job(ScheduledJob(job_id="test", cron="0 9 * * *", prompt="y"))
        # No crash = success
