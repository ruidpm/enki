"""Delegate a task to a persistent specialized team agent.

Tasks run in the background — execute() returns immediately with a job ID.
The team works independently, then reports back to Enki (the main agent).
Enki synthesizes the result and decides what to surface to the user.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from src.guardrails.confirmation_gate import REQUIRES_CONFIRM
from src.guardrails.cost_guard import CostGuardHook
from src.sub_agent import SubAgentRunner
from src.teams.store import TeamsStore

if TYPE_CHECKING:
    from src.jobs import JobRegistry

log = structlog.get_logger()

_EXCLUDED_TOOLS = {"spawn_team", "spawn_agent"}


class Notifier(Protocol):
    async def send(self, message: str) -> None: ...


class Agent(Protocol):
    async def run_turn(self, user_message: str) -> str: ...


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
    ) -> None:
        self._store = store
        self._config = config
        self._registry = tool_registry
        self._notifier = notifier
        self._job_registry: JobRegistry | None = job_registry
        self._cost_guard: CostGuardHook | None = cost_guard
        self._agent: Agent | None = None

    def set_agent(self, agent: Agent) -> None:
        """Wire the main agent after construction (avoids circular dep at build time)."""
        self._agent = agent

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

        # Route through Enki so he can synthesize and decide what to surface
        if self._agent is not None:
            report_prompt = (
                f"Team '{team['name']}' (id: {team_id}) has completed job `{job_id}`.\n\n"
                f"Their report:\n{raw_result}\n\n"
                f"Synthesize this and decide what's worth telling the user. "
                f"If it's urgent or actionable, send a Telegram message now. "
                f"If it's routine, a brief summary is fine."
            )
            try:
                sage_response = await self._agent.run_turn(report_prompt)
                await self._notifier.send(sage_response)
            except Exception as exc:
                log.error("spawn_team_sage_relay_error", job_id=job_id, error=str(exc))
                # Fallback: send raw result rather than silently drop it
                try:
                    await self._notifier.send(
                        f"**Team '{team_id}'** finished job `{job_id}` (relay error — raw result):\n\n{raw_result}"
                    )
                except Exception as fallback_exc:
                    log.error("spawn_team_fallback_send_failed", job_id=job_id, error=str(fallback_exc))
        else:
            # No agent wired (e.g. tests or CLI without agent set) — send raw
            status = "✅" if success else "❌"
            try:
                await self._notifier.send(f"{status} **Team '{team_id}'** finished job `{job_id}`\n\n{raw_result}")
            except Exception as exc:
                log.error("spawn_team_raw_send_failed", job_id=job_id, error=str(exc))
