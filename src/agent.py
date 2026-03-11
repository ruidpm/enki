"""Core agent loop — Claude API + tool dispatch + model routing."""
from __future__ import annotations

import asyncio
import pathlib
import re
import time
import uuid
from datetime import date
from enum import StrEnum
from typing import Any

import anthropic
import structlog

from .audit.db import AuditDB
from .audit.events import Tier1Event, Tier2Event
from .config import Settings
from .guardrails import GuardrailChain
from .guardrails.cost_guard import CostGuardHook
from .guardrails.loop_detector import LoopDetectorHook
from .guardrails.rate_limiter import RateLimiterHook
from .memory.store import MemoryStore
from .tools import Tool

log = structlog.get_logger()

# Cost per million tokens (input, output) in USD — approximate
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-6": (15.00, 75.00),
}

_HAIKU_KEYWORDS = re.compile(
    r"\b(list|show|what is|what are|what time|when is|status|remind|summarize briefly)\b",
    re.IGNORECASE,
)
_OPUS_KEYWORDS = re.compile(
    r"(/opus\b|use opus|architect|deep dive|detailed analysis|migration plan|"
    r"comprehensive|full plan|design system)",
    re.IGNORECASE,
)


class ModelTier(StrEnum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


def classify_complexity(message: str) -> ModelTier:
    """Lightweight heuristic model routing — no API call needed."""
    if _OPUS_KEYWORDS.search(message):
        return ModelTier.OPUS
    if _HAIKU_KEYWORDS.search(message):
        return ModelTier.HAIKU
    return ModelTier.SONNET


def _load_soul() -> str:
    """Load soul.md once at import time if present."""
    soul_path = pathlib.Path("soul.md")
    if soul_path.exists():
        return soul_path.read_text() + "\n\n---\n\n"
    return ""


_SOUL = _load_soul()


class Agent:
    def __init__(
        self,
        config: Settings,
        guardrails: GuardrailChain,
        memory: MemoryStore,
        tool_registry: dict[str, Tool],
        audit: AuditDB,
        cost_guard: CostGuardHook,
        loop_detector: LoopDetectorHook,
        rate_limiter: RateLimiterHook,
        session_id: str | None = None,
    ) -> None:
        self._config = config
        self._guardrails = guardrails
        self._memory = memory
        self._tools = tool_registry
        self._audit = audit
        self._cost_guard = cost_guard
        self._loop_detector = loop_detector
        self._rate_limiter = rate_limiter
        self._client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
        self._session_id = session_id or str(uuid.uuid4())
        self._conversation: list[dict[str, Any]] = []
        self._last_activity: float = time.monotonic()
        self._run_lock = asyncio.Lock()

        loop_detector.set_session(self._session_id)
        log.info("agent_init", session_id=self._session_id)

    @property
    def session_id(self) -> str:
        return self._session_id

    def new_session(self) -> None:
        """Clear conversation history and start a fresh session."""
        self._conversation.clear()
        self._session_id = str(uuid.uuid4())
        self._loop_detector.set_session(self._session_id)
        self._cost_guard.reset_session()
        self._last_activity = time.monotonic()
        log.info("agent_new_session", session_id=self._session_id)

    def _model_for_tier(self, tier: ModelTier) -> str:
        return {
            ModelTier.HAIKU: self._config.haiku_model,
            ModelTier.SONNET: self._config.default_model,
            ModelTier.OPUS: self._config.opus_model,
        }[tier]

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def _cost_usd(self, model: str, input_tokens: int, output_tokens: int) -> float:
        in_rate, out_rate = _MODEL_COSTS.get(model, (3.00, 15.00))
        return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000

    async def run_turn(self, user_message: str | list[dict[str, Any]]) -> str:
        """Process one user turn. Returns assistant response text.

        user_message can be a plain string or a list of content blocks
        (e.g. image + text for photo messages).
        """
        async with self._run_lock:
            return await self._run_turn_locked(user_message)

    async def _run_turn_locked(self, user_message: str | list[dict[str, Any]]) -> str:
        """Inner turn implementation — must only be called while _run_lock is held."""
        # Auto-reset session after configured idle period
        idle_hours = (time.monotonic() - self._last_activity) / 3600
        if idle_hours >= self._config.session_timeout_hours:
            log.info("agent_session_auto_reset", idle_hours=round(idle_hours, 2))
            self.new_session()
        self._last_activity = time.monotonic()

        # Reset per-turn state
        self._rate_limiter.reset()
        self._loop_detector.on_user_message()
        self._cost_guard.on_user_message()

        # Derive plain text for memory/audit/model-routing
        if isinstance(user_message, str):
            plain_text = user_message
        else:
            plain_text = " ".join(
                b.get("text", "") for b in user_message if b.get("type") == "text"
            ) or "[media]"

        # Record user message in memory + audit
        self._memory.append_turn(self._session_id, "user", plain_text)
        await self._audit.log_tier2(
            Tier2Event.USER_MESSAGE, self._session_id,
            {"length": len(plain_text)},
        )

        # Build memory context
        mem_context = self._memory.build_context(plain_text, self._session_id)
        today = date.today().strftime("%A, %B %-d, %Y")
        system_text = f"Today's date: {today}\n\n" + _SOUL
        if mem_context:
            system_text += f"## Memory context\n{mem_context}\n\n---\n\n"
        system_text += "You are a personal AI assistant. Use tools when needed."

        # Route model
        tier = classify_complexity(plain_text)
        model = self._model_for_tier(tier)
        log.info("model_selected", tier=tier, model=model)

        # Heal any orphaned tool_use block left by a previous failed turn
        if self._conversation:
            last = self._conversation[-1]
            if last.get("role") == "assistant":
                content = last.get("content", [])
                has_orphan = any(
                    getattr(b, "type", None) == "tool_use"
                    for b in (content if isinstance(content, list) else [])
                )
                if has_orphan:
                    self._conversation.pop()
                    log.warning("healed_orphaned_tool_use")

        # Add user message to conversation history
        self._conversation.append({"role": "user", "content": user_message})

        # Build cached system prompt + tool list (static — cache_control keeps tool list cached)
        system_block: list[dict[str, Any]] = [
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
        ]
        tools = self._tool_definitions()
        if tools:
            tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]

        # Agentic loop
        for _autonomous_turn in range(self._config.max_autonomous_turns + 1):
            response = await self._client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_block,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                messages=self._conversation,  # type: ignore[arg-type]
            )

            # Track cost
            usage = response.usage
            cost = self._cost_usd(model, usage.input_tokens, usage.output_tokens)
            self._cost_guard.record_llm_call(usage.input_tokens, usage.output_tokens, cost)
            await self._audit.log_tier2(
                Tier2Event.LLM_CALL, self._session_id,
                {
                    "model": model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost_usd": cost,
                },
            )

            # If no tool use, return text response
            if response.stop_reason != "tool_use":
                text = next(
                    (b.text for b in response.content if hasattr(b, "text")), ""
                )
                self._conversation.append({"role": "assistant", "content": response.content})
                self._memory.append_turn(self._session_id, "assistant", text)
                return text

            # Process tool calls
            self._conversation.append({"role": "assistant", "content": response.content})
            tool_results: list[dict[str, Any]] = []

            try:
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tool_name: str = block.name
                    params: dict[str, Any] = block.input

                    # Run guardrail chain
                    allow, reason = await self._guardrails.run(tool_name, params)

                    if not allow:
                        # Audit every guardrail block as a Tier1 security event
                        await self._audit.log_tier1(
                            Tier1Event.GUARDRAIL_BLOCK, self._session_id,
                            {"tool": tool_name, "reason": reason},
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"[BLOCKED by guardrail: {reason}]",
                            "is_error": True,
                        })
                        continue

                    # Safe tool lookup — guardrails verified it's registered, but guard anyway
                    tool = self._tools.get(tool_name)
                    if tool is None:
                        log.error("tool_not_found_post_guardrail", tool=tool_name)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"[Internal error: tool '{tool_name}' not found]",
                            "is_error": True,
                        })
                        continue

                    try:
                        result = await tool.execute(**params)
                    except Exception as exc:
                        result = f"[Tool error: {exc}]"
                        log.error("tool_error", tool=tool_name, error=str(exc))

                    await self._audit.log_tier2(
                        Tier2Event.TOOL_CALLED, self._session_id, {"tool": tool_name}
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            except Exception as exc:
                log.error("tool_loop_error", error=str(exc))
                # Synthesise error results for any tool_use blocks that didn't get one
                collected_ids = {r["tool_use_id"] for r in tool_results}
                for block in response.content:
                    if getattr(block, "type", None) == "tool_use":
                        block_id = getattr(block, "id", None)
                        if block_id and block_id not in collected_ids:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block_id,
                                "content": f"[interrupted: {exc}]",
                                "is_error": True,
                            })

            self._conversation.append({"role": "user", "content": tool_results})
            self._cost_guard.record_autonomous_turn()

        return "I reached the autonomous turn limit. Please provide further instructions."
