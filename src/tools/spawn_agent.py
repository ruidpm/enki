"""Sub-agent spawning tool — runs an isolated Claude agent with restricted tools."""
from __future__ import annotations

from typing import Any

import structlog

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

    def __init__(self, config: Any, tool_registry: dict[str, Any]) -> None:
        self._config = config
        self._registry = tool_registry
        self._active: int = 0  # asyncio is single-threaded: check+increment is atomic

    async def execute(self, **kwargs: Any) -> str:
        task: str = kwargs["task"]
        requested_tools: list[str] = kwargs.get("tools", [])
        model: str = kwargs.get("model", self._config.haiku_model)
        max_tokens: int = max(1, min(int(kwargs.get("max_tokens", 2048)), 8192))

        if self._active >= _MAX_CONCURRENT:
            return f"[BLOCKED] Max concurrent sub-agents ({_MAX_CONCURRENT}) reached."

        # Build restricted tool subset — snapshot at call time, never include spawn_agent
        subset = {
            name: tool
            for name, tool in self._registry.items()
            if name in requested_tools and name != "spawn_agent"
        }

        log.info(
            "sub_agent_spawning",
            task_preview=task[:100],
            tools=list(subset.keys()),
            model=model,
        )

        self._active += 1
        try:
            runner = SubAgentRunner(
                config=self._config,
                tools=subset,
                model=model,
                max_tokens=max_tokens,
            )
            result, _tokens = await runner.run(task)
        finally:
            self._active -= 1

        log.info("sub_agent_done", task_preview=task[:100])
        return result
