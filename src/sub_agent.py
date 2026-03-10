"""Sub-agent runner — isolated agentic loop with restricted tool subset."""
from __future__ import annotations

from typing import Any, Callable

import anthropic
import structlog

log = structlog.get_logger()

_MAX_STEPS = 80  # enough for multi-section plans and deep research


class SubAgentRunner:
    """Runs an isolated Claude agentic loop with a restricted set of tools."""

    def __init__(
        self,
        config: Any,
        tools: dict[str, Any],
        model: str,
        max_tokens: int = 4096,
        max_steps: int = _MAX_STEPS,
        system_prefix: str = "",
        label: str = "",  # e.g. "researcher/research" — shown in logs
        on_tokens: Callable[[int, int], None] | None = None,
    ) -> None:
        self._config = config
        self._tools = tools
        self._model = model
        self._max_tokens = max_tokens
        self._max_steps = max_steps
        self._system_prefix = system_prefix
        self._label = label
        self._on_tokens = on_tokens
        self._client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    def _tool_defs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    async def run(self, task: str) -> tuple[str, int]:
        """Run the sub-agent on the given task. Returns (response_text, total_tokens_used)."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        tool_defs = self._tool_defs()
        system = (
            (self._system_prefix + "\n\n") if self._system_prefix else ""
        ) + "You are a helpful sub-agent. Complete the given task and return a concise result. Plain text only — no markdown, no **bold**, no # headers, no bullet points with *, no backtick code blocks."

        total_tokens = 0

        for step in range(self._max_steps):
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                tools=tool_defs if tool_defs else [],
                messages=messages,
            )

            step_in = response.usage.input_tokens
            step_out = response.usage.output_tokens
            total_tokens += step_in + step_out
            if self._on_tokens is not None:
                self._on_tokens(step_in, step_out)

            # Collect tool use blocks
            tool_uses = [
                b for b in response.content
                if getattr(b, "type", None) == "tool_use"
            ]

            if response.stop_reason == "end_turn" or not tool_uses:
                texts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
                return "\n".join(texts) if texts else "(no response)", total_tokens

            # Add assistant message with tool use blocks
            messages.append({"role": "assistant", "content": response.content})

            # Execute each tool and collect results
            tool_results: list[dict[str, Any]] = []
            for tu in tool_uses:
                tool = self._tools.get(tu.name)
                if tool is None:
                    result_text = f"[ERROR] Tool '{tu.name}' not available in this sub-agent."
                    log.warning("sub_agent_unknown_tool", tool=tu.name)
                else:
                    try:
                        result_text = await tool.execute(**tu.input)
                    except Exception as exc:
                        result_text = f"[ERROR] {exc}"
                        log.error("sub_agent_tool_error", tool=tu.name, error=str(exc))

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": str(result_text),
                })

            messages.append({"role": "user", "content": tool_results})
            log.info(
                "sub_agent_step",
                label=self._label or "sub_agent",
                step=step + 1,
                tools_called=len(tool_uses),
                tools=[tu.name for tu in tool_uses],
            )

        log.warning("sub_agent_max_steps", label=self._label or "sub_agent", steps=self._max_steps)
        return f"[SUB-AGENT:{self._label or '?'}] Max steps ({self._max_steps}) reached.", total_tokens
