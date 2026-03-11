"""Proactive scheduler — APScheduler cron jobs for briefings and alerts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import structlog
from apscheduler.jobstores.base import JobLookupError  # type: ignore[import-untyped]
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from src.schedule.store import ScheduleStore

log = structlog.get_logger()


class SchedulerAgent(Protocol):
    async def run_turn(self, user_message: str) -> str: ...


class SchedulerNotifier(Protocol):
    async def send(self, message: str) -> None: ...


@dataclass
class ScheduledJob:
    job_id: str
    cron: str          # standard 5-field cron: "0 8 * * *"
    prompt: str        # what to ask the agent
    enabled: bool = True


class Scheduler:
    def __init__(
        self,
        agent: SchedulerAgent,
        notifier: SchedulerNotifier,
        store: ScheduleStore | None = None,
    ) -> None:
        self._agent = agent
        self._notifier = notifier
        self._store = store
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
                    minute=minute, hour=hour,
                    day=dom, month=month, day_of_week=dow,
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
                await self._notifier.send(
                    f"Job `{job.job_id}` failed: {exc}"
                )
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
            prompt=(
                "Check for any tasks due in the next 48 hours and remind me "
                "of upcoming deadlines."
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
