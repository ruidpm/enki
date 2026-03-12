"""Proactive scheduler — APScheduler cron jobs for briefings and alerts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from apscheduler.jobstores.base import JobLookupError  # type: ignore[import-untyped]
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from croniter import croniter  # type: ignore[import-untyped]

from src.interfaces.agent_protocol import AgentProtocol
from src.interfaces.notifier import Notifier

if TYPE_CHECKING:
    from src.schedule.store import ScheduleStore

log = structlog.get_logger()


@dataclass
class ScheduledJob:
    job_id: str
    cron: str  # standard 5-field cron: "0 8 * * *"
    prompt: str  # what to ask the agent
    enabled: bool = True


@dataclass
class MissedJob:
    """A scheduled job that should have fired during a downtime window."""

    job_id: str
    cron: str
    prompt: str
    expected_time: int  # unix timestamp of when it should have fired


class Scheduler:
    def __init__(
        self,
        agent: AgentProtocol,
        notifier: Notifier,
        store: ScheduleStore | None = None,
        timezone: str = "UTC",
    ) -> None:
        self._agent = agent
        self._notifier = notifier
        self._store = store
        self._tz = timezone
        self._scheduler = AsyncIOScheduler()
        self.jobs: dict[str, ScheduledJob] = {}

    def load_from_store(self) -> None:
        """Load all enabled jobs from the store into APScheduler. Call at startup."""
        if self._store is None:
            return
        for row in self._store.list_enabled():
            job = ScheduledJob(
                job_id=row["job_id"],
                cron=row["cron"],
                prompt=row["prompt"],
                enabled=True,
            )
            self.add_job(job)
        log.info("scheduler_loaded_from_store", job_count=len(self.jobs))

    def add_job(self, job: ScheduledJob) -> None:
        """Register or overwrite a scheduled job."""
        self.jobs[job.job_id] = job
        try:
            self._scheduler.remove_job(job.job_id)
        except (JobLookupError, KeyError):
            pass  # Job didn't exist yet — expected on first add
        if job.enabled:
            minute, hour, dom, month, dow = job.cron.split()
            self._scheduler.add_job(
                self._run_job,
                trigger=CronTrigger(
                    minute=minute,
                    hour=hour,
                    day=dom,
                    month=month,
                    day_of_week=dow,
                    timezone=self._tz,
                ),
                args=[job],
                id=job.job_id,
                name=job.job_id,
                replace_existing=True,
            )
            log.info("job_scheduled", job_id=job.job_id, cron=job.cron)

    def remove_job(self, job_id: str) -> None:
        """Remove a job from APScheduler and the in-memory registry."""
        self.jobs.pop(job_id, None)
        try:
            self._scheduler.remove_job(job_id)
        except (JobLookupError, KeyError):
            pass  # Job didn't exist — nothing to remove

    def set_job_enabled(self, job_id: str, enabled: bool) -> None:
        """Pause or resume a job without losing its config."""
        job = self.jobs.get(job_id)
        if job is None:
            return
        job.enabled = enabled
        if enabled:
            self.add_job(job)
        else:
            try:
                self._scheduler.remove_job(job_id)
            except (JobLookupError, KeyError):
                pass  # Already removed or never added

    def calculate_missed_jobs(self, since: int) -> list[MissedJob]:
        """Calculate which enabled jobs should have fired between `since` and now.

        Uses croniter to iterate cron schedules and find firings that fell
        within the downtime window [since, now].
        """
        now = int(datetime.now(tz=UTC).timestamp())
        if since >= now:
            return []

        missed: list[MissedJob] = []
        start_dt = datetime.fromtimestamp(since, tz=UTC)

        for job in self.jobs.values():
            if not job.enabled:
                continue
            cron = croniter(job.cron, start_dt)
            while True:
                next_fire = cron.get_next(datetime)
                fire_ts = int(next_fire.timestamp())
                if fire_ts > now:
                    break
                missed.append(
                    MissedJob(
                        job_id=job.job_id,
                        cron=job.cron,
                        prompt=job.prompt,
                        expected_time=fire_ts,
                    )
                )

        missed.sort(key=lambda m: m.expected_time)
        return missed

    async def run_job_now(self, job_id: str) -> bool:
        """Run a job immediately (catch-up execution). Returns False if job not found."""
        job = self.jobs.get(job_id)
        if job is None:
            return False
        await self._run_job(job)
        return True

    async def _run_job(self, job: ScheduledJob) -> None:
        log.info("job_running", job_id=job.job_id)
        try:
            response = await self._agent.run_turn(job.prompt)
            await self._notifier.send(response)
            if self._store is not None:
                self._store.record_run(job.job_id)
        except Exception as exc:
            log.error("job_error", job_id=job.job_id, error=str(exc))
            try:
                await self._notifier.send(f"Job `{job.job_id}` failed: {exc}")
            except Exception as notify_exc:
                log.warning(
                    "job_notify_fallback_failed",
                    job_id=job.job_id,
                    error=str(notify_exc),
                )

    def start(self) -> None:
        self._scheduler.start()
        log.info("scheduler_started", job_count=len(self.jobs))

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)


def default_jobs() -> list[ScheduledJob]:
    """Default proactive jobs — seeded into the schedule store on first run."""
    return [
        ScheduledJob(
            job_id="morning_briefing",
            cron="0 8 * * *",
            prompt=(
                "Give me a morning briefing: tasks due today or overdue, "
                "any calendar events today, and anything I should be aware of."
            ),
        ),
        ScheduledJob(
            job_id="deadline_check",
            cron="0 17 * * *",
            prompt=("Check for any tasks due in the next 48 hours and remind me of upcoming deadlines."),
        ),
        ScheduledJob(
            job_id="audit_verification",
            cron="0 6 * * *",
            prompt=(
                "Run an audit verification check. Use the audit tool to verify "
                "the integrity of the audit trail and check for anomalies in the "
                "last 24 hours. Report any issues found."
            ),
        ),
        ScheduledJob(
            job_id="eod_team_report",
            cron="0 21 * * *",
            prompt=(
                "Generate end-of-day team report. Call team_report to get all metrics, "
                "then format a concise summary: each team's tasks today, success rate, "
                "tokens used vs budget."
            ),
        ),
    ]
