"""In-memory job registry — tracks running background tasks.

Jobs (CCC, run_pipeline, spawn_team) register here at start and update their
status as they progress. When the process restarts, all jobs are gone — this
is intentional. For durable history, see: pipeline_store, team_tasks, audit DB.

Thread-safe via a simple lock (asyncio tasks share the same thread but the
lock prevents concurrent dict mutation from interleaved awaits).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

# Cost per token by model substring (input_rate, output_rate) in USD
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "haiku":  (0.80e-6, 4.00e-6),
    "sonnet": (3.00e-6, 15.0e-6),
    "opus":   (15.0e-6, 75.0e-6),
}
_DEFAULT_COST = _MODEL_COSTS["haiku"]


def _cost_rates(model: str) -> tuple[float, float]:
    m = model.lower()
    for key, rates in _MODEL_COSTS.items():
        if key in m:
            return rates
    return _DEFAULT_COST


class JobStatus:
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobRegistry:
    """Singleton-safe in-memory registry. One instance lives in main.py."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def start(
        self,
        job_id: str,
        *,
        job_type: str,
        description: str,
        model: str = "",
    ) -> None:
        """Register a new job as RUNNING."""
        self._jobs[job_id] = {
            "job_id": job_id,
            "type": job_type,
            "description": description,
            "status": JobStatus.RUNNING,
            "stage": None,
            "error": None,
            "started_at": time.monotonic(),
            "ended_at": None,
            "_task": None,  # asyncio.Task — set via set_task()
            "model": model,
            "tokens_in": 0,
            "tokens_out": 0,
        }

    def set_task(self, job_id: str, task: "asyncio.Task[Any]") -> None:
        """Store the asyncio Task so it can be cancelled later."""
        if job_id in self._jobs:
            self._jobs[job_id]["_task"] = task

    def cancel(self, job_id: str) -> bool:
        """Cancel the asyncio Task for a job. Returns True if cancellation was requested."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        task: asyncio.Task[Any] | None = job.get("_task")
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def update_stage(self, job_id: str, stage: str) -> None:
        """Update the current stage label for a running job."""
        if job_id in self._jobs:
            self._jobs[job_id]["stage"] = stage

    def add_tokens(self, job_id: str, input_tokens: int, output_tokens: int) -> None:
        """Increment token counters for a running job (called after each API step)."""
        job = self._jobs.get(job_id)
        if job is not None:
            job["tokens_in"] += input_tokens
            job["tokens_out"] += output_tokens

    def finish(self, job_id: str, *, success: bool, error: str | None = None) -> None:
        """Mark a job as DONE or FAILED."""
        if job_id not in self._jobs:
            return
        self._jobs[job_id]["status"] = JobStatus.DONE if success else JobStatus.FAILED
        self._jobs[job_id]["ended_at"] = time.monotonic()
        if error:
            self._jobs[job_id]["error"] = error

    def get(self, job_id: str) -> dict[str, Any] | None:
        """Return a copy of the job record, with elapsed_s computed."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        return self._enrich(dict(job))

    def list_running(self) -> list[dict[str, Any]]:
        """Return all currently running jobs, sorted by start time."""
        return sorted(
            [self._enrich(dict(j)) for j in self._jobs.values()
             if j["status"] == JobStatus.RUNNING],
            key=lambda j: j["started_at"],
        )

    def list_all(self) -> list[dict[str, Any]]:
        """Return all jobs (running + finished), sorted by start time."""
        return sorted(
            [self._enrich(dict(j)) for j in self._jobs.values()],
            key=lambda j: j["started_at"],
        )

    @staticmethod
    def _enrich(job: dict[str, Any]) -> dict[str, Any]:
        end = job["ended_at"] or time.monotonic()
        job["elapsed_s"] = end - job["started_at"]
        tokens_in = job.get("tokens_in", 0)
        tokens_out = job.get("tokens_out", 0)
        job["tokens_total"] = tokens_in + tokens_out
        rate_in, rate_out = _cost_rates(job.get("model", ""))
        job["cost_usd"] = tokens_in * rate_in + tokens_out * rate_out
        return job
