"""Delegate a task to a persistent specialized team agent.

Tasks run in the background — execute() returns immediately with a job ID.
The team works independently, then reports back to Enki (the main agent).
Enki synthesizes the result and decides what to surface to the user.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

import anthropic
import structlog

from src.constants import REQUIRES_CONFIRM
from src.guardrails.cost_guard import CostGuardHook
from src.interfaces.notifier import Notifier
from src.sub_agent import SubAgentRunner
from src.teams.store import TeamsStore

if TYPE_CHECKING:
    from src.jobs import JobRegistry

log = structlog.get_logger()

_EXCLUDED_TOOLS = {"spawn_team", "spawn_agent"}


class SpawnTeamTool:
    name = "spawn_team"
    description = (
        "Delegate a task to a persistent specialized team agent. "
        "The team works independently in the background — you get a job ID immediately. "
        "When the team finishes, their report goes to Enki who will decide what to tell you. "
        "Use team_report to see available teams and their status."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "Team identifier (use team_report to list teams)",
            },
            "task": {
                "type": "string",
                "description": "Complete, self-contained task description for the team",
            },
        },
        "required": ["team_id", "task"],
    }

    def __init__(
        self,
        store: TeamsStore,
        config: Any,
        tool_registry: dict[str, Any],
        notifier: Notifier,
        job_registry: JobRegistry | None = None,
        cost_guard: CostGuardHook | None = None,
        anthropic_client: anthropic.AsyncAnthropic | None = None,
        summary_model: str = "",
    ) -> None:
        self._store = store
        self._config = config
        self._registry = tool_registry
        self._notifier = notifier
        self._job_registry: JobRegistry | None = job_registry
        self._cost_guard: CostGuardHook | None = cost_guard
        self._client: anthropic.AsyncAnthropic | None = anthropic_client
        self._summary_model = summary_model

    async def execute(self, **kwargs: Any) -> str:
        team_id: str = kwargs["team_id"]
        task: str = kwargs["task"]

        team = self._store.get_team(team_id)
        if team is None or not team["active"]:
            return f"[ERROR] Team '{team_id}' not found or inactive. Use team_report to list active teams."

        used = self._store.monthly_tokens_used(team_id)
        budget = team["monthly_token_budget"]
        if used >= budget:
            return f"[BLOCKED] Team '{team_id}' has exhausted its monthly token budget ({used}/{budget} tokens used)."

        allowed_tool_names: set[str] = set(team["tools"]) - _EXCLUDED_TOOLS - REQUIRES_CONFIRM
        subset = {name: tool for name, tool in self._registry.items() if name in allowed_tool_names}

        job_id = uuid.uuid4().hex[:8]
        log.info(
            "spawn_team_delegating",
            team_id=team_id,
            job_id=job_id,
            task_preview=task[:100],
            tools=list(subset.keys()),
        )

        if self._job_registry is not None:
            self._job_registry.start(
                job_id,
                job_type="team",
                description=f"{team_id}: {task[:60]}",
                model=self._config.haiku_model,
            )

        bg_task = asyncio.create_task(self._run_background(job_id, team_id, team, subset, task))

        if self._job_registry is not None:
            self._job_registry.set_task(job_id, bg_task)

        return (
            f"Task delegated to team '{team_id}' (job {job_id}). "
            f"They're working on it independently — I'll process their report when they're done."
        )

    async def _run_background(
        self,
        job_id: str,
        team_id: str,
        team: dict[str, Any],
        subset: dict[str, Any],
        task: str,
    ) -> None:
        start = time.monotonic()
        success = True
        raw_result = ""

        try:

            def _on_tokens(inp: int, out: int) -> None:
                if self._job_registry is not None:
                    self._job_registry.add_tokens(job_id, inp, out)

            def _on_cost(inp: int, out: int, cost: float) -> None:
                if self._cost_guard is not None:
                    self._cost_guard.record_llm_call(inp, out, cost)

            runner = SubAgentRunner(
                config=self._config,
                tools=subset,
                model=self._config.haiku_model,
                max_steps=getattr(self._config, "sub_agent_max_steps", 80),
                system_prefix=team["role"],
                label=team_id,
                on_tokens=_on_tokens,
                on_cost=_on_cost,
            )
            raw_result, tokens_used = await runner.run(task)
        except asyncio.CancelledError:
            log.info("spawn_team_cancelled", team_id=team_id, job_id=job_id)
            if self._job_registry is not None:
                self._job_registry.finish(job_id, success=False, error="Cancelled")
            raise
        except Exception as exc:
            success = False
            raw_result = f"[ERROR] Task failed: {exc}"
            tokens_used = 0
            log.error("spawn_team_error", team_id=team_id, job_id=job_id, error=str(exc))

        duration = time.monotonic() - start

        self._store.log_task(
            team_id=team_id,
            task=task,
            result=raw_result,
            tokens_used=tokens_used,
            success=success,
            duration_s=duration,
        )

        if self._job_registry is not None:
            self._job_registry.finish(job_id, success=success)

        log.info("spawn_team_done", team_id=team_id, job_id=job_id, success=success, duration_s=duration)

        # Summarize via stateless API call (no conversation pollution)
        status_icon = "✅" if success else "❌"
        prefix = f"{status_icon} **Team '{team_id}'** finished job `{job_id}`"

        if self._client is not None:
            report_prompt = (
                f"Team '{team['name']}' (id: {team_id}) has completed job `{job_id}`.\n\n"
                f"Their report:\n{raw_result[:4000]}\n\n"
                f"Synthesize this into 2-3 bullet points for a Telegram notification. Be concise."
            )
            try:
                resp = await self._client.messages.create(
                    model=self._summary_model,
                    max_tokens=300,
                    messages=[{"role": "user", "content": report_prompt}],
                )
                block = resp.content[0]
                summary = block.text if hasattr(block, "text") else str(block)

                # Store result in registry
                if self._job_registry is not None:
                    self._job_registry.set_result(job_id, summary=summary)

                await self._notifier.send(f"{prefix}\n\n{summary}")
            except Exception as exc:
                log.error("spawn_team_summary_error", job_id=job_id, error=str(exc))
                # Fallback: send raw result rather than silently drop it
                try:
                    await self._notifier.send(f"{prefix} (summary error — raw result):\n\n{raw_result}")
                except Exception as fallback_exc:
                    log.error("spawn_team_fallback_send_failed", job_id=job_id, error=str(fallback_exc))
        else:
            # No client configured (e.g. tests or CLI) — send raw
            try:
                await self._notifier.send(f"{prefix}\n\n{raw_result}")
            except Exception as exc:
                log.error("spawn_team_raw_send_failed", job_id=job_id, error=str(exc))
