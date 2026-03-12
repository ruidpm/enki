"""Job status tool — query running and recent background jobs."""

from __future__ import annotations

import math
from typing import Any

from src.jobs import JobRegistry, JobStatus


def _fmt_elapsed(seconds: float) -> str:
    s = math.floor(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s}s"


def _fmt_job(job: dict[str, Any]) -> str:
    status_icon = {
        JobStatus.RUNNING: "⟳",
        JobStatus.DONE: "✓",
        JobStatus.FAILED: "✗",
    }.get(job["status"], "?")

    stage = f" [{job['stage']}]" if job["stage"] else ""
    elapsed = _fmt_elapsed(job["elapsed_s"])
    tokens = job.get("tokens_total", 0)
    cost = job.get("cost_usd", 0.0)
    cost_str = f"  |  {tokens:,} tok  ~${cost:.4f}" if tokens > 0 else ""
    line = f"  {status_icon} {job['job_id']}  {job['type']}{stage}  {elapsed}{cost_str}"
    line += f"\n     {job['description'][:80]}"
    if job["status"] == JobStatus.FAILED and job["error"]:
        line += f"\n     Error: {job['error'][:120]}"
    return line


class JobStatusTool:
    name = "job_status"
    description = (
        "Show status of background jobs (Claude Code runs, pipelines, team tasks). "
        "Use when asked 'what are you working on?', 'check on the pipeline', etc. "
        "Shows running jobs by default. Pass show_all=true to include completed jobs, "
        "or job_id to inspect a specific job."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Optional: show details for a specific job ID",
            },
            "show_all": {
                "type": "boolean",
                "description": "Include completed and failed jobs (default: running only)",
            },
        },
    }

    def __init__(self, registry: JobRegistry) -> None:
        self._registry = registry

    async def execute(self, **kwargs: Any) -> str:
        job_id: str | None = kwargs.get("job_id")
        show_all: bool = kwargs.get("show_all", False)

        if job_id:
            job = self._registry.get(job_id)
            if job is None:
                return f"[ERROR] Job '{job_id}' not found."
            return _fmt_job(job)

        jobs = self._registry.list_all() if show_all else self._registry.list_running()

        if not jobs:
            if show_all:
                return "No jobs recorded this session."
            return "No background jobs currently running. I'm idle."

        label = "All jobs this session" if show_all else "Running jobs"
        lines = [f"{label} ({len(jobs)}):\n"]
        lines.extend(_fmt_job(j) for j in jobs)
        return "\n".join(lines)
