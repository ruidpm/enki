"""Tests for scheduler timezone support."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.config import Settings
from src.scheduler import ScheduledJob, Scheduler


class TestTimezoneConfig:
    """Settings should expose a timezone field."""

    def test_default_timezone_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.timezone == "UTC"

    def test_override_timezone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("TIMEZONE", "Europe/Lisbon")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.timezone == "Europe/Lisbon"


class TestSchedulerTimezone:
    """Scheduler should pass timezone to CronTrigger."""

    def test_scheduler_accepts_timezone(self) -> None:
        agent = AsyncMock()
        notifier = AsyncMock()
        scheduler = Scheduler(agent=agent, notifier=notifier, timezone="Europe/Lisbon")
        assert scheduler._tz == "Europe/Lisbon"

    def test_scheduler_default_utc(self) -> None:
        agent = AsyncMock()
        notifier = AsyncMock()
        scheduler = Scheduler(agent=agent, notifier=notifier)
        assert scheduler._tz == "UTC"

    def test_add_job_uses_timezone(self) -> None:
        """CronTrigger should receive the timezone parameter."""
        agent = AsyncMock()
        notifier = AsyncMock()
        scheduler = Scheduler(agent=agent, notifier=notifier, timezone="Europe/Lisbon")

        job = ScheduledJob(job_id="test", cron="0 8 * * *", prompt="morning")
        scheduler.add_job(job)

        # Verify the job was added to APScheduler
        ap_job = scheduler._scheduler.get_job("test")
        assert ap_job is not None
        assert str(ap_job.trigger.timezone) == "Europe/Lisbon"

    def test_missed_jobs_uses_timezone(self) -> None:
        """calculate_missed_jobs should work with timezone-aware scheduler."""
        agent = AsyncMock()
        notifier = AsyncMock()
        scheduler = Scheduler(agent=agent, notifier=notifier, timezone="Europe/Lisbon")

        job = ScheduledJob(job_id="test", cron="0 8 * * *", prompt="morning")
        scheduler.add_job(job)

        # Should not crash with timezone set
        missed = scheduler.calculate_missed_jobs(since=0)
        # Will have many missed jobs since epoch — just check it works
        assert isinstance(missed, list)
