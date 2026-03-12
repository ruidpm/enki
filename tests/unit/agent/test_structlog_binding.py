"""Tests for structlog context binding — H-13."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_session_id_bound_during_turn(tmp_path: Any) -> None:
    """session_id must be bound to structlog context during _run_turn_locked."""
    from src.agent import Agent
    from src.audit.db import AuditDB
    from src.config import Settings
    from src.guardrails import GuardrailChain
    from src.guardrails.cost_guard import CostGuardHook
    from src.guardrails.loop_detector import LoopDetectorHook
    from src.guardrails.rate_limiter import RateLimiterHook

    config = MagicMock(spec=Settings)
    config.anthropic_api_key = "test-key"
    config.default_model = "claude-sonnet-4-6"
    config.haiku_model = "claude-haiku-4-5-20251001"
    config.opus_model = "claude-opus-4-6"
    config.session_timeout_hours = 8
    config.max_autonomous_turns = 5
    config.max_tokens_per_session = 100_000
    config.max_daily_cost_usd = 10.0
    config.max_monthly_cost_usd = 100.0
    config.max_llm_calls_per_session = 50
    config.loop_detection_threshold = 3
    config.max_tool_calls_per_turn = 10
    config.max_context_tokens = 120_000

    audit = AuditDB(tmp_path / "audit.db")
    memory = MagicMock()
    memory.build_context = MagicMock(return_value="")
    memory.append_turn = MagicMock()

    cost_guard = CostGuardHook(
        max_tokens_per_session=100_000,
        max_daily_cost_usd=10.0,
        max_monthly_cost_usd=100.0,
        max_llm_calls_per_session=50,
        max_autonomous_turns=5,
    )
    loop_detector = LoopDetectorHook(threshold=3)
    rate_limiter = RateLimiterHook(max_per_turn=10)
    chain = GuardrailChain([])

    agent = Agent(
        config=config,
        guardrails=chain,
        memory=memory,
        tool_registry={},
        audit=audit,
        cost_guard=cost_guard,
        loop_detector=loop_detector,
        rate_limiter=rate_limiter,
    )

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Hello!"

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [text_block]
    response.usage = MagicMock(input_tokens=10, output_tokens=5)

    agent._client.messages.create = AsyncMock(return_value=response)

    with (
        patch("src.agent.structlog.contextvars.bind_contextvars") as mock_bind,
        patch("src.agent.structlog.contextvars.unbind_contextvars") as mock_unbind,
    ):
        await agent.run_turn("hi")

        mock_bind.assert_called_once_with(session_id=agent.session_id)
        mock_unbind.assert_called_once_with("session_id")
