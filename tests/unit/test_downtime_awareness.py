"""Tests for downtime awareness & missed job recovery.

Covers:
1. Scheduler.calculate_missed_jobs returns correct missed jobs
2. Scheduler.run_job_now triggers immediate job execution
3. Startup routes downtime through agent.run_turn, not raw bot.send
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.scheduler import MissedJob, ScheduledJob, Scheduler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> MagicMock:
    a = MagicMock()
    a.run_turn = AsyncMock(return_value="Briefing response")
    return a


@pytest.fixture
def notifier() -> MagicMock:
    n = MagicMock()
    n.send = AsyncMock()
    return n


@pytest.fixture
def scheduler(agent: MagicMock, notifier: MagicMock) -> Scheduler:
    s = Scheduler(agent=agent, notifier=notifier)
    return s


# ---------------------------------------------------------------------------
# calculate_missed_jobs
# ---------------------------------------------------------------------------


class TestCalculateMissedJobs:
    def test_no_jobs_returns_empty(self, scheduler: Scheduler) -> None:
        result = scheduler.calculate_missed_jobs(since=int(time.time()) - 3600)
        assert result == []

    def test_disabled_job_not_reported(self, scheduler: Scheduler) -> None:
        job = ScheduledJob(job_id="test", cron="0 8 * * *", prompt="test", enabled=False)
        scheduler.add_job(job)
        result = scheduler.calculate_missed_jobs(since=int(time.time()) - 86400)
        assert all(m.job_id != "test" for m in result)

    def test_detects_missed_hourly_job(self, scheduler: Scheduler) -> None:
        """A job running every hour should be detected as missed after 2h gap."""
        job = ScheduledJob(job_id="hourly", cron="0 * * * *", prompt="hourly task", enabled=True)
        scheduler.add_job(job)
        # Gap of 3 hours
        since = int(time.time()) - 3 * 3600
        result = scheduler.calculate_missed_jobs(since=since)
        assert len(result) >= 2  # at least 2 missed firings in 3h
        assert all(isinstance(m, MissedJob) for m in result)
        assert all(m.job_id == "hourly" for m in result)

    def test_missed_job_has_expected_fire_time(self, scheduler: Scheduler) -> None:
        job = ScheduledJob(job_id="hourly", cron="0 * * * *", prompt="hourly task", enabled=True)
        scheduler.add_job(job)
        now = int(time.time())
        since = now - 3 * 3600
        result = scheduler.calculate_missed_jobs(since=since)
        for m in result:
            assert since <= m.expected_time <= now
            assert m.job_id == "hourly"
            assert m.cron == "0 * * * *"

    def test_no_false_positives_for_recent_gap(self, scheduler: Scheduler) -> None:
        """A daily 8am job should not be missed if the gap is only 30 seconds."""
        job = ScheduledJob(job_id="daily", cron="0 8 * * *", prompt="daily task", enabled=True)
        scheduler.add_job(job)
        since = int(time.time()) - 30  # 30 seconds ago
        result = scheduler.calculate_missed_jobs(since=since)
        assert result == []

    def test_multiple_jobs_all_detected(self, scheduler: Scheduler) -> None:
        """Multiple jobs with missed firings should all be reported."""
        scheduler.add_job(ScheduledJob(job_id="a", cron="0 * * * *", prompt="task a", enabled=True))
        scheduler.add_job(ScheduledJob(job_id="b", cron="30 * * * *", prompt="task b", enabled=True))
        since = int(time.time()) - 2 * 3600
        result = scheduler.calculate_missed_jobs(since=since)
        job_ids = {m.job_id for m in result}
        assert "a" in job_ids
        assert "b" in job_ids


# ---------------------------------------------------------------------------
# run_job_now
# ---------------------------------------------------------------------------


class TestRunJobNow:
    @pytest.mark.asyncio
    async def test_run_job_now_executes(self, scheduler: Scheduler, agent: MagicMock, notifier: MagicMock) -> None:
        job = ScheduledJob(job_id="briefing", cron="0 8 * * *", prompt="Give briefing", enabled=True)
        scheduler.add_job(job)
        await scheduler.run_job_now("briefing")
        agent.run_turn.assert_awaited_once_with("Give briefing")
        notifier.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_job_now_unknown_job(self, scheduler: Scheduler) -> None:
        result = await scheduler.run_job_now("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_run_job_now_records_in_store(self, agent: MagicMock, notifier: MagicMock) -> None:
        store = MagicMock()
        store.record_run = MagicMock()
        s = Scheduler(agent=agent, notifier=notifier, store=store)
        job = ScheduledJob(job_id="test", cron="0 8 * * *", prompt="test prompt", enabled=True)
        s.add_job(job)
        await s.run_job_now("test")
        store.record_run.assert_called_once_with("test")


# ---------------------------------------------------------------------------
# MissedJob dataclass
# ---------------------------------------------------------------------------


class TestMissedJob:
    def test_missed_job_fields(self) -> None:
        m = MissedJob(job_id="test", cron="0 8 * * *", prompt="Do thing", expected_time=1000)
        assert m.job_id == "test"
        assert m.cron == "0 8 * * *"
        assert m.prompt == "Do thing"
        assert m.expected_time == 1000
