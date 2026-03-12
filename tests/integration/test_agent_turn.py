"""Integration test — full agent turn with mocked Anthropic client."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import Agent
from src.audit.db import AuditDB
from src.config import Settings
from src.guardrails import GuardrailChain
from src.guardrails.allowlist import AllowlistHook
from src.guardrails.cost_guard import CostGuardHook
from src.guardrails.loop_detector import LoopDetectorHook
from src.guardrails.rate_limiter import RateLimiterHook
from src.memory.store import MemoryStore


@pytest.fixture
def config() -> Settings:
    return Settings(
        anthropic_api_key="test",
        telegram_bot_token="test",
        brave_search_api_key="test",
        telegram_chat_id="123",
    )


@pytest.fixture
def agent(config: Settings, tmp_path: Path) -> Agent:
    audit = AuditDB(tmp_path / "audit.db")
    memory = MemoryStore(tmp_path / "memory.db")
    cost_guard = CostGuardHook(
        max_tokens_per_session=100_000,
        max_daily_cost_usd=5.0,
        max_monthly_cost_usd=30.0,
        max_llm_calls_per_session=50,
        max_autonomous_turns=5,
    )
    loop_detector = LoopDetectorHook()
    rate_limiter = RateLimiterHook()
    registry: dict[str, Any] = {}
    chain = GuardrailChain([AllowlistHook(registry), rate_limiter, loop_detector, cost_guard])
    return Agent(
        config=config,
        guardrails=chain,
        memory=memory,
        tool_registry=registry,
        audit=audit,
        cost_guard=cost_guard,
        loop_detector=loop_detector,
        rate_limiter=rate_limiter,
    )


def _mock_text_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    response.usage.input_tokens = 10
    response.usage.output_tokens = 5
    return response


@pytest.mark.asyncio
async def test_simple_text_turn(agent: Agent) -> None:
    with patch.object(
        agent._client.messages, "create", new=AsyncMock(return_value=_mock_text_response("Hello! How can I help?"))
    ):
        result = await agent.run_turn("hi")
    assert result == "Hello! How can I help?"


@pytest.mark.asyncio
async def test_turn_logs_to_memory(agent: Agent) -> None:
    with patch.object(agent._client.messages, "create", new=AsyncMock(return_value=_mock_text_response("Done."))):
        await agent.run_turn("remember this")
    turns = agent._memory.get_recent_turns(agent.session_id)
    assert any(t["role"] == "user" for t in turns)
    assert any(t["role"] == "assistant" for t in turns)


@pytest.mark.asyncio
async def test_turn_records_llm_cost(agent: Agent) -> None:
    with patch.object(agent._client.messages, "create", new=AsyncMock(return_value=_mock_text_response("ok"))):
        await agent.run_turn("test")
    assert agent._cost_guard.session_tokens > 0
