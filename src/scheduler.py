"""Proactive scheduler — APScheduler cron jobs for briefings and alerts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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
        # Backup config — injected via set_backup_config()
        self._backup_data_dir: Path | None = None
        self._backup_memory_dir: Path | None = None
        self._backup_repo: str = ""

    def set_backup_config(
        self,
        *,
        data_dir: Path,
        memory_dir: Path,
        backup_repo: str,
    ) -> None:
        """Inject paths and repo for cloud backup job."""
        self._backup_data_dir = data_dir
        self._backup_memory_dir = memory_dir
        self._backup_repo = backup_repo

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
            handler = self._run_backup if job.job_id == "cloud_backup" else self._run_job
            minute, hour, dom, month, dow = job.cron.split()
            self._scheduler.add_job(
                handler,
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

    async def _run_backup(self, _job: ScheduledJob) -> None:
        """Run cloud backup directly — no LLM needed."""
        from src.backup import run_backup

        if not self._backup_repo or self._backup_data_dir is None or self._backup_memory_dir is None:
            log.info("backup_skipped", reason="backup not configured")
            return
        log.info("backup_starting")
        result = await run_backup(
            data_dir=self._backup_data_dir,
            memory_dir=self._backup_memory_dir,
            backup_repo=self._backup_repo,
        )
        try:
            await self._notifier.send(result)
        except Exception as exc:
            log.warning("backup_notify_failed", error=str(exc))

    def start(self) -> None:
        self._scheduler.start()
        log.info("scheduler_started", job_count=len(self.jobs))

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)


def default_jobs() -> list[ScheduledJob]:
    """Default proactive jobs — seeded into the schedule store on startup.

    These are upserted every startup so prompt improvements propagate automatically.
    """
    return [
        ScheduledJob(
            job_id="morning_briefing",
            cron="0 8 * * *",
            prompt=(
                "Morning briefing. Gather data and present a structured report.\n\n"
                "## Sections (use these exact headers, skip empty ones):\n"
                "*Calendar* — today's events from calendar_read (days=1). "
                "If calendar is unavailable, say so in one line and move on.\n"
                "*Tasks* — open tasks due today or overdue (ignore anything due >2 days out).\n"
                "*Email* — unread emails needing action (if email tool is available). "
                "Skip this section entirely if email is not configured.\n"
                "*Blockers* — anything waiting on someone else or stuck.\n"
                "*Follow\\-ups* — open items from `follow_ups` tool \\(action=list\\). "
                "Show items older than 2 days with how long they've been waiting.\n\n"
                "## Classification (tag each item):\n"
                "DISPATCH = you can handle autonomously, no input needed\n"
                "PREP = you should prepare materials/research for me\n"
                "YOURS = requires my decision or action\n"
                "SKIP = not worth attention today\n\n"
                "Format: one line per item, tag in brackets. Example:\n"
                "[YOURS] Team standup at 10am — prepare talking points?\n"
                "[DISPATCH] Overdue task: renew API token — want me to handle it?\n\n"
                "End with: *That's it for today.*\n"
                "Keep it tight — no filler, no preamble."
            ),
        ),
        ScheduledJob(
            job_id="deadline_check",
            cron="0 17 * * *",
            prompt=(
                "Check for tasks due in the next 48 hours or overdue. "
                "Do NOT mention tasks with deadlines more than 7 days away — "
                "those are not urgent. Only surface what needs attention NOW."
            ),
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
            job_id="eod_report",
            cron="0 21 * * *",
            prompt=(
                "End-of-day report. Summarize what was accomplished today:\n"
                "1. Review today's conversation history — what did we work on? "
                "What was built, fixed, or decided?\n"
                "2. Check team_report for team activity (tasks, success rate, cost).\n"
                "3. List any open blockers or things to follow up on tomorrow.\n"
                "Keep it concise — bullet points, no fluff."
            ),
        ),
        ScheduledJob(
            job_id="cloud_backup",
            cron="0 3 * * *",
            prompt="cloud_backup",  # not sent to LLM — routed to _run_backup
        ),
    ]
