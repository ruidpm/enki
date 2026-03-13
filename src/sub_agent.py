"""Sub-agent runner — isolated agentic loop with restricted tool subset."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic
import anthropic.types
import structlog
from anthropic.types import TextBlock, ToolUseBlock

log = structlog.get_logger()

_MAX_STEPS = 80  # enough for multi-section plans and deep research
_MAX_API_RETRIES = 3
_RETRY_DELAYS = (1.0, 2.0, 4.0)
_PREVIEW_CHARS = 500  # truncation limit for tool input/output previews
_MAX_TOOL_RESULT_CHARS = 10_000  # cap tool output to prevent context bloat


@dataclass(frozen=True)
class ToolCallRecord:
    """Record of a single tool invocation within a sub-agent step."""

    name: str
    input_preview: str  # first _PREVIEW_CHARS chars of JSON input
    output_preview: str  # first _PREVIEW_CHARS chars of result


@dataclass(frozen=True)
class StepRecord:
    """Audit record for a single sub-agent step (one API call + tool executions)."""

    step_number: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    tools: list[ToolCallRecord] = field(default_factory=list)
    duration_ms: int = 0


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
        on_cost: Callable[[int, int, float], None] | None = None,
        on_step: Callable[[StepRecord], None] | None = None,
        max_tool_result_chars: int = _MAX_TOOL_RESULT_CHARS,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self._config = config
        self._tools = tools
        self._model = model
        self._max_tokens = max_tokens
        self._max_steps = max_steps
        self._system_prefix = system_prefix
        self._label = label
        self._on_tokens = on_tokens
        self._on_cost = on_cost
        self._on_step = on_step
        self._max_tool_result_chars = max_tool_result_chars
        self._cancel_check = cancel_check
        self._client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    async def _api_call_with_retry(self, **kwargs: Any) -> anthropic.types.Message:
        """Call messages.create with retry on transient errors."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_API_RETRIES):
            try:
                return await asyncio.wait_for(
                    self._client.messages.create(**kwargs),
                    timeout=60.0,
                )
            except (anthropic.APIConnectionError, anthropic.APITimeoutError, TimeoutError) as exc:
                last_exc = exc
                if attempt < _MAX_API_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    log.warning(
                        "sub_agent_api_retry",
                        label=self._label,
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

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
        _base = "You are a helpful sub-agent. Complete the given task and return a concise result."
        system: str | list[dict[str, Any]] = ((self._system_prefix + "\n\n") if self._system_prefix else "") + _base

        # Cache: system prompt block + last tool definition
        system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if tool_defs:
            tool_defs = [*tool_defs[:-1], {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}]

        total_tokens = 0

        for step in range(self._max_steps):
            # Check for cancellation before each step
            if self._cancel_check is not None and self._cancel_check():
                log.info("sub_agent_cancelled", label=self._label, step=step + 1)
                return "[CANCELLED] Sub-agent stopped by cancel check.", total_tokens

            step_start = time.monotonic()

            try:
                response = await self._api_call_with_retry(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=system,
                    tools=tool_defs if tool_defs else [],
                    messages=messages,
                )
            except (anthropic.APIConnectionError, anthropic.APITimeoutError, TimeoutError) as exc:
                log.error("sub_agent_api_exhausted", label=self._label, error=str(exc))
                return f"[ERROR] API unavailable after {_MAX_API_RETRIES} retries: {exc}", total_tokens

            step_in = response.usage.input_tokens
            step_out = response.usage.output_tokens
            step_cost = 0.0
            total_tokens += step_in + step_out
            if self._on_tokens is not None:
                self._on_tokens(step_in, step_out)
            if self._on_cost is not None:
                from src.costs import model_cost_usd

                _cc = getattr(response.usage, "cache_creation_input_tokens", None)
                _cr = getattr(response.usage, "cache_read_input_tokens", None)
                cache_create = _cc if isinstance(_cc, int) else 0
                cache_read = _cr if isinstance(_cr, int) else 0
                step_cost = model_cost_usd(
                    self._model,
                    step_in,
                    step_out,
                    cache_creation_input_tokens=cache_create,
                    cache_read_input_tokens=cache_read,
                )
                self._on_cost(step_in, step_out, step_cost)

            # Collect tool use blocks
            tool_uses = [b for b in response.content if isinstance(b, ToolUseBlock)]

            if response.stop_reason == "end_turn" or not tool_uses:
                # Final step — record it before returning
                duration_ms = int((time.monotonic() - step_start) * 1000)
                if self._on_step is not None:
                    self._on_step(
                        StepRecord(
                            step_number=step + 1,
                            input_tokens=step_in,
                            output_tokens=step_out,
                            cost_usd=step_cost,
                            tools=[],
                            duration_ms=duration_ms,
                        )
                    )
                texts = [b.text for b in response.content if isinstance(b, TextBlock)]
                return "\n".join(texts) if texts else "(no response)", total_tokens

            # Add assistant message with tool use blocks
            messages.append({"role": "assistant", "content": response.content})

            # Execute each tool and collect results
            tool_results: list[dict[str, Any]] = []
            tool_call_records: list[ToolCallRecord] = []
            for tu in tool_uses:
                input_preview = json.dumps(tu.input, default=str)[:_PREVIEW_CHARS]
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

                result_str = str(result_text)
                if len(result_str) > self._max_tool_result_chars:
                    result_str = result_str[: self._max_tool_result_chars] + "\n[TRUNCATED]"
                tool_call_records.append(
                    ToolCallRecord(
                        name=tu.name,
                        input_preview=input_preview,
                        output_preview=result_str[:_PREVIEW_CHARS],
                    )
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": result_str,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

            # Record step audit data
            duration_ms = int((time.monotonic() - step_start) * 1000)
            if self._on_step is not None:
                self._on_step(
                    StepRecord(
                        step_number=step + 1,
                        input_tokens=step_in,
                        output_tokens=step_out,
                        cost_usd=step_cost,
                        tools=tool_call_records,
                        duration_ms=duration_ms,
                    )
                )

            log.info(
                "sub_agent_step",
                label=self._label or "sub_agent",
                step=step + 1,
                tools_called=len(tool_uses),
                tools=[tu.name for tu in tool_uses],
                tool_inputs=[{tu.name: json.dumps(tu.input, default=str)[:120]} for tu in tool_uses],
                step_tokens=step_in + step_out,
                cumulative_tokens=total_tokens,
            )

        log.warning(
            "sub_agent_max_steps",
            label=self._label or "sub_agent",
            steps=self._max_steps,
            total_tokens=total_tokens,
        )
        return (
            f"[INCOMPLETE: max steps reached] Sub-agent '{self._label or '?'}' "
            f"hit the {self._max_steps}-step limit. Results may be partial."
        ), total_tokens
