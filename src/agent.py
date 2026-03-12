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
import anthropic.types
import structlog
import structlog.contextvars

from .audit.db import AuditDB
from .audit.events import Tier2Event
from .config import Settings
from .costs import model_cost_usd
from .guardrails import GuardrailChain
from .guardrails.cost_guard import CostGuardHook
from .guardrails.loop_detector import LoopDetectorHook
from .guardrails.rate_limiter import RateLimiterHook
from .memory.store import MemoryStore
from .tools import Tool

log = structlog.get_logger()

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

    @property
    def daily_cost_usd(self) -> float:
        return self._cost_guard.daily_cost_usd

    @property
    def monthly_cost_usd(self) -> float:
        return self._cost_guard.monthly_cost_usd

    @property
    def session_tokens(self) -> int:
        return self._cost_guard.session_tokens

    @property
    def audit(self) -> AuditDB:
        return self._audit

    def _estimate_tokens(self) -> int:
        """Estimate total conversation tokens using chars/4 heuristic."""
        total_chars = 0
        for msg in self._conversation:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(str(block.get("content", "")))
                        total_chars += len(str(block.get("text", "")))
                    else:
                        total_chars += len(str(block))
        return total_chars // 4

    def _prune_conversation(self) -> None:
        """Drop oldest turns when conversation exceeds max_context_tokens.

        Keeps at least 3 recent user/assistant pairs (6 messages).
        Logs a warning when approaching the limit (>= 80%).
        """
        max_tokens = self._config.max_context_tokens
        estimated = self._estimate_tokens()
        min_keep = 6  # 3 recent pairs

        # Warn at 80%
        if estimated >= int(max_tokens * 0.8):
            log.warning(
                "context_window_approaching_limit",
                estimated_tokens=estimated,
                max_tokens=max_tokens,
                pct=round(estimated / max_tokens * 100),
                conversation_len=len(self._conversation),
            )

        # Prune if over limit
        if estimated > max_tokens and len(self._conversation) > min_keep:
            # Drop messages from the front, 2 at a time (user+assistant pairs)
            while self._estimate_tokens() > max_tokens and len(self._conversation) > min_keep:
                self._conversation.pop(0)
            log.info(
                "context_window_pruned",
                remaining_messages=len(self._conversation),
                estimated_tokens=self._estimate_tokens(),
            )

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

    _MAX_API_RETRIES = 3
    _RETRY_DELAYS = (1.0, 2.0, 4.0)

    async def _api_call_with_retry(self, **kwargs: Any) -> anthropic.types.Message:
        """Call messages.create with retry on transient errors."""
        last_exc: Exception | None = None
        for attempt in range(self._MAX_API_RETRIES):
            try:
                return await asyncio.wait_for(
                    self._client.messages.create(**kwargs),
                    timeout=60.0,
                )
            except (anthropic.APIConnectionError, anthropic.APITimeoutError, TimeoutError) as exc:
                last_exc = exc
                if attempt < self._MAX_API_RETRIES - 1:
                    delay = self._RETRY_DELAYS[attempt]
                    log.warning(
                        "api_retry",
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    async def run_turn(self, user_message: str | list[dict[str, Any]]) -> str:
        """Process one user turn. Returns assistant response text.

        user_message can be a plain string or a list of content blocks
        (e.g. image + text for photo messages).
        """
        async with self._run_lock:
            return await self._run_turn_locked(user_message)

    async def _run_turn_locked(self, user_message: str | list[dict[str, Any]]) -> str:
        """Inner turn implementation — must only be called while _run_lock is held."""
        structlog.contextvars.bind_contextvars(session_id=self._session_id)
        try:
            return await self._run_turn_inner(user_message)
        finally:
            structlog.contextvars.unbind_contextvars("session_id")

    async def _run_turn_inner(self, user_message: str | list[dict[str, Any]]) -> str:
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
            plain_text = " ".join(b.get("text", "") for b in user_message if b.get("type") == "text") or "[media]"

        # Record user message in memory + audit
        self._memory.append_turn(self._session_id, "user", plain_text)
        await self._audit.log_tier2(
            Tier2Event.USER_MESSAGE,
            self._session_id,
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
                has_orphan = any(getattr(b, "type", None) == "tool_use" for b in (content if isinstance(content, list) else []))
                if has_orphan:
                    self._conversation.pop()
                    log.warning("healed_orphaned_tool_use")

        # Prune conversation if approaching context limit
        self._prune_conversation()

        # Add user message to conversation history
        self._conversation.append({"role": "user", "content": user_message})

        # Build cached system prompt + tool list (static — cache_control keeps tool list cached)
        system_block: list[dict[str, Any]] = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
        tools = self._tool_definitions()
        if tools:
            tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]

        # Agentic loop
        for _autonomous_turn in range(self._config.max_autonomous_turns + 1):
            try:
                response = await self._api_call_with_retry(
                    model=model,
                    max_tokens=4096,
                    system=system_block,
                    tools=tools,
                    messages=self._conversation,
                )
            except (anthropic.APIConnectionError, anthropic.APITimeoutError, TimeoutError) as exc:
                log.error("api_exhausted_retries", error=str(exc))
                return "I'm having trouble reaching the API right now. Please try again in a moment."

            # Track cost
            usage = response.usage
            cost = model_cost_usd(model, usage.input_tokens, usage.output_tokens)
            self._cost_guard.record_llm_call(usage.input_tokens, usage.output_tokens, cost)
            await self._audit.log_tier2(
                Tier2Event.LLM_CALL,
                self._session_id,
                {
                    "model": model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost_usd": cost,
                },
            )

            # If no tool use, return text response
            if response.stop_reason != "tool_use":
                text = next((b.text for b in response.content if hasattr(b, "text")), "")
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

                    # Record guardrail decision (both allow and block)
                    await self._audit.log_tool_call(
                        tool_name=tool_name,
                        params=params,
                        allowed=allow,
                        block_reason=reason,
                        session_id=self._session_id,
                    )

                    if not allow:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": f"[BLOCKED by guardrail: {reason}]",
                                "is_error": True,
                            }
                        )
                        continue

                    # Safe tool lookup — guardrails verified it's registered, but guard anyway
                    tool = self._tools.get(tool_name)
                    if tool is None:
                        log.error("tool_not_found_post_guardrail", tool=tool_name)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": f"[Internal error: tool '{tool_name}' not found]",
                                "is_error": True,
                            }
                        )
                        continue

                    try:
                        result = await tool.execute(**params)
                    except Exception as exc:
                        result = f"[Tool error: {exc}]"
                        log.error("tool_error", tool=tool_name, error=str(exc))

                    result_preview = result[:200] if isinstance(result, str) else str(result)[:200]
                    await self._audit.log_tier2(
                        Tier2Event.TOOL_CALLED,
                        self._session_id,
                        {"tool": tool_name, "result_preview": result_preview},
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            except Exception as exc:
                log.error("tool_loop_error", error=str(exc))
                # Synthesise error results for any tool_use blocks that didn't get one
                collected_ids = {r["tool_use_id"] for r in tool_results}
                for block in response.content:
                    if getattr(block, "type", None) == "tool_use":
                        block_id = getattr(block, "id", None)
                        if block_id and block_id not in collected_ids:
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block_id,
                                    "content": f"[interrupted: {exc}]",
                                    "is_error": True,
                                }
                            )

            self._conversation.append({"role": "user", "content": tool_results})
            self._cost_guard.record_autonomous_turn()

        return "I reached the autonomous turn limit. Please provide further instructions."
