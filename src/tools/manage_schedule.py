"""Schedule management tools — list and manage persistent cron jobs.

Split into two tools (read/write) so list_schedule needs no confirmation
while manage_schedule always goes through the guardrail confirmation gate.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from src.schedule.store import ScheduleStore

if TYPE_CHECKING:
    from src.scheduler import Scheduler

log = structlog.get_logger()

# 5-field cron: each field is digits, *, commas, slashes, or hyphens
_CRON_FIELD = r"[0-9*,/\-]+"
_CRON_RE = re.compile(rf"^{_CRON_FIELD}\s+{_CRON_FIELD}\s+{_CRON_FIELD}\s+{_CRON_FIELD}\s+{_CRON_FIELD}$")


def _validate_cron(cron: str) -> str | None:
    """Return error message if invalid, None if valid."""
    if not _CRON_RE.match(cron.strip()):
        return (
            f"Invalid cron expression '{cron}'. "
            "Must be 5 space-separated fields (minute hour dom month dow). "
            "Example: '0 7 * * *' = every day at 7am."
        )
    return None


class ListScheduleTool:
    name = "list_schedule"
    description = (
        "List all scheduled recurring jobs — cron expression, enabled status, "
        "last run time, and run count. Read-only, no confirmation needed."
    )
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, store: ScheduleStore) -> None:
        self._store = store

    async def execute(self, **kwargs: Any) -> str:
        jobs = self._store.list_all()
        if not jobs:
            return "No scheduled jobs. Use manage_schedule to add one."

        lines = ["## Scheduled Jobs\n"]
        lines.append("| ID | Cron | Status | Last run | Runs | Prompt |")
        lines.append("|----|------|--------|----------|------|--------|")
        for j in jobs:
            status = "✅ active" if j["enabled"] else "⏸ paused"
            last_run = j["last_run"] or "never"
            prompt_preview = (j["prompt"][:50] + "…") if len(j["prompt"]) > 50 else j["prompt"]
            lines.append(f"| {j['job_id']} | `{j['cron']}` | {status} | {last_run} | {j['run_count']} | {prompt_preview} |")
        return "\n".join(lines)


class ManageScheduleTool:
    name = "manage_schedule"
    description = (
        "Add, pause, resume, or remove a recurring scheduled job. "
        "Jobs run Enki on a cron schedule with a given prompt. "
        "To delegate to a team on a schedule, include 'spawn_team' in the prompt. "
        "Requires user confirmation. Use list_schedule to see current jobs."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "update", "pause", "resume", "remove"],
                "description": (
                    "add: create new job | update: edit prompt/cron of existing job"
                    " | pause: disable | resume: re-enable | remove: delete"
                ),
            },
            "job_id": {
                "type": "string",
                "description": "Unique job identifier (slug, e.g. 'oil-daily')",
            },
            "cron": {
                "type": "string",
                "description": "5-field cron expression, e.g. '0 7 * * *' = daily at 7am",
            },
            "prompt": {
                "type": "string",
                "description": "What Enki should do when the job fires (for 'add' and 'update' actions)",
            },
        },
        "required": ["action", "job_id"],
    }

    def __init__(self, store: ScheduleStore) -> None:
        self._store = store
        self._scheduler: Scheduler | None = None

    def set_scheduler(self, scheduler: Scheduler) -> None:
        """Wire the live Scheduler after it's built (avoids circular dep at startup)."""
        self._scheduler = scheduler

    async def execute(self, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "")
        job_id: str = kwargs.get("job_id", "")

        if not job_id:
            return "[ERROR] job_id is required."

        if action == "add":
            return self._add(job_id, kwargs)
        elif action == "update":
            return self._update(job_id, kwargs)
        elif action == "pause":
            return self._pause(job_id)
        elif action == "resume":
            return self._resume(job_id)
        elif action == "remove":
            return self._remove(job_id)
        else:
            return f"[ERROR] Unknown action '{action}'. Use add, update, pause, resume, or remove."

    def _add(self, job_id: str, kwargs: dict[str, Any]) -> str:
        cron: str = kwargs.get("cron") or ""
        prompt: str = kwargs.get("prompt") or ""

        err = _validate_cron(cron)
        if err:
            return f"[ERROR] {err}"
        if not prompt:
            return "[ERROR] prompt is required for action 'add'."

        self._store.upsert(job_id, cron.strip(), prompt)

        note = ""
        if self._scheduler is not None:
            from src.scheduler import ScheduledJob

            job = ScheduledJob(job_id=job_id, cron=cron.strip(), prompt=prompt)
            self._scheduler.add_job(job)
        else:
            note = " (will activate on next restart)"

        log.info("schedule_added", job_id=job_id, cron=cron)
        return f"Job '{job_id}' added{note}.\nCron: {cron}\nPrompt: {prompt}"

    def _update(self, job_id: str, kwargs: dict[str, Any]) -> str:
        existing = self._store.get(job_id)
        if existing is None:
            return f"[ERROR] Job '{job_id}' not found. Use list_schedule to see available jobs."

        new_prompt: str = kwargs.get("prompt") or existing["prompt"]
        new_cron: str = (kwargs.get("cron") or existing["cron"]).strip()

        err = _validate_cron(new_cron)
        if err:
            return f"[ERROR] {err}"

        self._store.upsert(job_id, new_cron, new_prompt)

        note = ""
        if self._scheduler is not None:
            from src.scheduler import ScheduledJob

            job = ScheduledJob(job_id=job_id, cron=new_cron, prompt=new_prompt)
            self._scheduler.remove_job(job_id)
            self._scheduler.add_job(job)
        else:
            note = " (will take effect on next restart)"

        log.info("schedule_updated", job_id=job_id)
        return f"Job '{job_id}' updated{note}.\nCron: {new_cron}\nPrompt: {new_prompt}"

    def _pause(self, job_id: str) -> str:
        if self._store.get(job_id) is None:
            return f"[ERROR] Job '{job_id}' not found. Use list_schedule to see available jobs."
        self._store.set_enabled(job_id, False)
        if self._scheduler is not None:
            self._scheduler.set_job_enabled(job_id, False)
        log.info("schedule_paused", job_id=job_id)
        return f"Job '{job_id}' paused."

    def _resume(self, job_id: str) -> str:
        if self._store.get(job_id) is None:
            return f"[ERROR] Job '{job_id}' not found. Use list_schedule to see available jobs."
        self._store.set_enabled(job_id, True)
        if self._scheduler is not None:
            self._scheduler.set_job_enabled(job_id, True)
        log.info("schedule_resumed", job_id=job_id)
        return f"Job '{job_id}' resumed."

    def _remove(self, job_id: str) -> str:
        if self._store.get(job_id) is None:
            return f"[ERROR] Job '{job_id}' not found. Use list_schedule to see available jobs."
        self._store.remove(job_id)
        if self._scheduler is not None:
            self._scheduler.remove_job(job_id)
        log.info("schedule_removed", job_id=job_id)
        return f"Job '{job_id}' removed."
