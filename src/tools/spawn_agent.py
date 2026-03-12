"""Sub-agent spawning tool — runs an isolated Claude agent with restricted tools."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from src.guardrails.confirmation_gate import REQUIRES_CONFIRM
from src.guardrails.cost_guard import CostGuardHook
from src.sub_agent import SubAgentRunner

log = structlog.get_logger()

_MAX_CONCURRENT = 5


class SpawnAgentTool:
    name = "spawn_agent"
    description = (
        "Spawn an isolated sub-agent to handle a self-contained task in parallel. "
        "Useful for: parallel web research, long document analysis, isolated tasks "
        "that should not pollute the main conversation. "
        "Sub-agents cannot spawn other agents. Max 5 concurrent sub-agents. "
        "Provide an explicit list of tools the sub-agent is allowed to use."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Complete, self-contained task description for the sub-agent",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tool names the sub-agent may use (e.g. ['web_search', 'notes'])",
            },
            "model": {
                "type": "string",
                "description": "Model to use (default: haiku for cost efficiency)",
            },
            "max_tokens": {
                "type": "integer",
                "default": 2048,
                "description": "Max tokens for the sub-agent response",
            },
        },
        "required": ["task", "tools"],
    }

    def __init__(
        self,
        config: Any,
        tool_registry: dict[str, Any],
        cost_guard: CostGuardHook | None = None,
    ) -> None:
        self._config = config
        self._registry = tool_registry
        self._cost_guard: CostGuardHook | None = cost_guard
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def execute(self, **kwargs: Any) -> str:
        task: str = kwargs["task"]
        requested_tools: list[str] = kwargs.get("tools", [])
        model: str = kwargs.get("model", self._config.haiku_model)
        max_tokens: int = max(1, min(int(kwargs.get("max_tokens", 2048)), 8192))

        if self._semaphore.locked():
            return f"[BLOCKED] Max concurrent sub-agents ({_MAX_CONCURRENT}) reached."

        # Build restricted tool subset — snapshot at call time
        # Never include spawn_agent or tools requiring user confirmation
        subset = {
            name: tool
            for name, tool in self._registry.items()
            if name in requested_tools and name != "spawn_agent" and name not in REQUIRES_CONFIRM
        }

        log.info(
            "sub_agent_spawning",
            task_preview=task[:100],
            tools=list(subset.keys()),
            model=model,
        )

        async with self._semaphore:

            def _on_cost(inp: int, out: int, cost: float) -> None:
                if self._cost_guard is not None:
                    self._cost_guard.record_llm_call(inp, out, cost)

            runner = SubAgentRunner(
                config=self._config,
                tools=subset,
                model=model,
                max_tokens=max_tokens,
                on_cost=_on_cost,
            )
            result, _tokens = await runner.run(task)

        log.info("sub_agent_done", task_preview=task[:100])
        return result
