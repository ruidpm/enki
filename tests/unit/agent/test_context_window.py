"""Tests for sliding-window conversation context management (C-03)."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from src.agent import Agent
from src.audit.db import AuditDB
from src.config import Settings
from src.guardrails import GuardrailChain
from src.guardrails.allowlist import AllowlistHook
from src.guardrails.cost_guard import CostGuardHook
from src.guardrails.loop_detector import LoopDetectorHook
from src.guardrails.rate_limiter import RateLimiterHook
from src.memory.store import MemoryStore


def _make_config(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "anthropic_api_key": "test",
        "telegram_bot_token": "test",
        "brave_search_api_key": "test",
        "telegram_chat_id": "123",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_agent(tmp_path: Any, config: Settings | None = None) -> Agent:
    config = config or _make_config()
    audit = AuditDB(tmp_path / "audit.db")
    memory = MemoryStore(tmp_path / "memory.db")
    registry: dict[str, Any] = {}
    cost_guard = CostGuardHook(
        max_tokens_per_session=100_000,
        max_daily_cost_usd=5.0,
        max_monthly_cost_usd=30.0,
        max_llm_calls_per_session=50,
        max_autonomous_turns=5,
    )
    loop_detector = LoopDetectorHook()
    rate_limiter = RateLimiterHook()
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


# ---------------------------------------------------------------------------
# C-03: Conversation pruning when exceeding token limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_pruned_when_exceeding_token_limit(tmp_path: Any) -> None:
    """Conversation is pruned to fit within max_context_tokens."""
    config = _make_config(max_context_tokens=1000)  # small limit
    agent = _make_agent(tmp_path, config)

    # Seed conversation with many turns (each ~200+ chars → ~50+ tokens)
    for i in range(20):
        agent._conversation.append({"role": "user", "content": f"message {i} " * 40})
        agent._conversation.append({"role": "assistant", "content": f"response {i} " * 40})

    with patch.object(agent._client.messages, "create", new=AsyncMock(
        return_value=_mock_text_response("pruned reply")
    )):
        result = await agent.run_turn("new question")

    assert result == "pruned reply"
    # Conversation should have been pruned — fewer messages than the 40 we seeded + 2 new
    assert len(agent._conversation) < 42


@pytest.mark.asyncio
async def test_most_recent_turns_always_kept(tmp_path: Any) -> None:
    """At minimum the last 3 user/assistant pairs are kept after pruning."""
    config = _make_config(max_context_tokens=1000)  # small limit
    agent = _make_agent(tmp_path, config)

    # Seed 10 turns
    for i in range(10):
        agent._conversation.append({"role": "user", "content": f"msg {i} " * 50})
        agent._conversation.append({"role": "assistant", "content": f"rsp {i} " * 50})

    with patch.object(agent._client.messages, "create", new=AsyncMock(
        return_value=_mock_text_response("ok")
    )):
        await agent.run_turn("final question")

    # Must have at least 3 prior pairs (6 messages) + new user + new assistant = 8
    # But the new user message is added AFTER pruning, so we check for at least
    # the 3 pairs (6) + the new user (1) + new assistant (1) = 8
    # The pruning keeps min 3 pairs = 6 messages from history
    assert len(agent._conversation) >= 8


@pytest.mark.asyncio
async def test_system_prompt_and_memory_always_preserved(tmp_path: Any) -> None:
    """System prompt and memory are always passed to the API (not part of conversation list)."""
    config = _make_config(max_context_tokens=1000)
    agent = _make_agent(tmp_path, config)

    # Seed some history (enough to exceed limit so pruning runs)
    for i in range(20):
        agent._conversation.append({"role": "user", "content": f"msg {i} " * 40})
        agent._conversation.append({"role": "assistant", "content": f"rsp {i} " * 40})

    captured_kwargs: dict[str, Any] = {}

    async def capture_create(**kwargs: Any) -> MagicMock:
        captured_kwargs.update(kwargs)
        return _mock_text_response("ok")

    with patch.object(agent._client.messages, "create", new=capture_create):
        await agent.run_turn("test")

    # System block must always be present
    assert "system" in captured_kwargs
    system = captured_kwargs["system"]
    assert len(system) > 0
    # First system block should contain text
    assert "text" in system[0]


@pytest.mark.asyncio
async def test_warning_logged_when_approaching_limit(tmp_path: Any) -> None:
    """A warning is logged when conversation approaches the context limit."""
    config = _make_config(max_context_tokens=1000)
    agent = _make_agent(tmp_path, config)

    # Seed enough to trigger pruning
    for i in range(20):
        agent._conversation.append({"role": "user", "content": f"msg {i} " * 40})
        agent._conversation.append({"role": "assistant", "content": f"rsp {i} " * 40})

    with structlog.testing.capture_logs() as cap_logs, patch.object(
        agent._client.messages, "create", new=AsyncMock(return_value=_mock_text_response("ok"))
    ):
        await agent.run_turn("test")

    assert any(
        "context" in e.get("event", "").lower() and e.get("log_level") == "warning"
        for e in cap_logs
    )


@pytest.mark.asyncio
async def test_no_pruning_when_under_limit(tmp_path: Any) -> None:
    """Conversation is not pruned when under the token limit."""
    config = _make_config(max_context_tokens=120_000)  # large limit
    agent = _make_agent(tmp_path, config)

    # Add a small conversation
    agent._conversation.append({"role": "user", "content": "hello"})
    agent._conversation.append({"role": "assistant", "content": "hi there"})

    with patch.object(agent._client.messages, "create", new=AsyncMock(
        return_value=_mock_text_response("ok")
    )):
        await agent.run_turn("follow up")

    # Should be 4 messages: original pair + new pair
    assert len(agent._conversation) == 4


@pytest.mark.asyncio
async def test_config_has_max_context_tokens_default() -> None:
    """Settings has max_context_tokens with default of 120000."""
    config = _make_config()
    assert hasattr(config, "max_context_tokens")
    assert config.max_context_tokens == 120_000
