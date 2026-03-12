"""Tests for agent conversation safety: orphan healing, exception prevention, lock serialisation."""

from __future__ import annotations

import asyncio
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


def _make_config() -> Settings:
    return Settings(
        anthropic_api_key="test",
        telegram_bot_token="test",
        brave_search_api_key="test",
        telegram_chat_id="123",
    )


def _make_agent(tmp_path: Any, registry: dict[str, Any] | None = None) -> Agent:
    config = _make_config()
    audit = AuditDB(tmp_path / "audit.db")
    memory = MemoryStore(tmp_path / "memory.db")
    registry = registry or {}
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


def _make_orphaned_tool_use_block(tool_id: str = "toolu_orphan") -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = "some_tool"
    return block


# ---------------------------------------------------------------------------
# Bug 1A: Healing — orphaned tool_use in conversation history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heals_orphaned_tool_use_on_next_turn(tmp_path: Any) -> None:
    """An orphaned assistant tool_use message is popped before the next turn."""
    agent = _make_agent(tmp_path)

    # Inject orphaned assistant message (tool_use with no following tool_result)
    orphan_block = _make_orphaned_tool_use_block()
    agent._conversation.append({"role": "assistant", "content": [orphan_block]})
    assert len(agent._conversation) == 1

    with patch.object(agent._client.messages, "create", new=AsyncMock(return_value=_mock_text_response("Recovered fine."))):
        result = await agent.run_turn("hello after crash")

    assert result == "Recovered fine."
    # Conversation should be [user, assistant] — no orphan
    assert len(agent._conversation) == 2
    assert agent._conversation[0]["role"] == "user"
    assert agent._conversation[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_heals_orphaned_tool_use_logs_warning(tmp_path: Any) -> None:
    """Healing an orphan emits a warning log event (healed_orphaned_tool_use)."""
    import structlog.testing

    agent = _make_agent(tmp_path)
    orphan_block = _make_orphaned_tool_use_block()
    agent._conversation.append({"role": "assistant", "content": [orphan_block]})

    with (
        structlog.testing.capture_logs() as cap_logs,
        patch.object(agent._client.messages, "create", new=AsyncMock(return_value=_mock_text_response("ok"))),
    ):
        await agent.run_turn("next message")

    assert any(e.get("log_level") == "warning" and "healed" in e.get("event", "") for e in cap_logs)


@pytest.mark.asyncio
async def test_no_healing_when_history_clean(tmp_path: Any) -> None:
    """Normal conversation (no orphan) is not modified before the turn."""
    agent = _make_agent(tmp_path)

    # Pre-seed a well-formed exchange
    user_block = MagicMock()
    user_block.type = "text"
    user_block.text = "previous message"
    agent._conversation.append({"role": "user", "content": "previous message"})
    agent._conversation.append({"role": "assistant", "content": [user_block]})

    with patch.object(agent._client.messages, "create", new=AsyncMock(return_value=_mock_text_response("next response"))):
        result = await agent.run_turn("follow up")

    assert result == "next response"
    # Should be 4 messages: prev user, prev assistant, new user, new assistant
    assert len(agent._conversation) == 4


# ---------------------------------------------------------------------------
# Bug 1B: Prevention — tool loop exception still appends tool_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_loop_exception_still_appends_tool_results(tmp_path: Any) -> None:
    """If a guardrail/audit raises mid-loop, tool_results are still appended to conversation."""
    agent = _make_agent(tmp_path)

    # Make a tool_use response
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "toolu_abc"
    tool_block.name = "unregistered_tool"
    tool_block.input = {}

    tool_response = MagicMock()
    tool_response.stop_reason = "tool_use"
    tool_response.content = [tool_block]
    tool_response.usage.input_tokens = 10
    tool_response.usage.output_tokens = 5

    text_response = _mock_text_response("done after error")

    # Guardrail raises on the first call
    async def exploding_guardrail(tool_name: str, params: Any) -> tuple[bool, str]:
        raise RuntimeError("guardrail exploded")

    agent._guardrails.run = exploding_guardrail  # type: ignore[method-assign]

    with patch.object(agent._client.messages, "create", new=AsyncMock(side_effect=[tool_response, text_response])):
        await agent.run_turn("do something")

    # Conversation must be well-formed: user, assistant(tool_use), user(tool_result), assistant(text)
    roles = [m["role"] for m in agent._conversation]
    assert roles == ["user", "assistant", "user", "assistant"]
    # The synthetic tool_result must reference the tool_use id
    tool_result_msg = agent._conversation[2]
    assert isinstance(tool_result_msg["content"], list)
    ids = [r["tool_use_id"] for r in tool_result_msg["content"]]
    assert "toolu_abc" in ids


@pytest.mark.asyncio
async def test_tool_loop_exception_marks_result_as_error(tmp_path: Any) -> None:
    """Synthetic tool_result injected on exception has is_error=True."""
    agent = _make_agent(tmp_path)

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "toolu_xyz"
    tool_block.name = "some_tool"
    tool_block.input = {}

    tool_response = MagicMock()
    tool_response.stop_reason = "tool_use"
    tool_response.content = [tool_block]
    tool_response.usage.input_tokens = 10
    tool_response.usage.output_tokens = 5

    text_response = _mock_text_response("recovered")

    async def exploding_guardrail(tool_name: str, params: Any) -> tuple[bool, str]:
        raise RuntimeError("boom")

    agent._guardrails.run = exploding_guardrail  # type: ignore[method-assign]

    with patch.object(agent._client.messages, "create", new=AsyncMock(side_effect=[tool_response, text_response])):
        await agent.run_turn("trigger")

    tool_result_msg = agent._conversation[2]
    result = next(r for r in tool_result_msg["content"] if r["tool_use_id"] == "toolu_xyz")
    assert result.get("is_error") is True


# ---------------------------------------------------------------------------
# Bug 2: Concurrent turns serialised — conversation stays well-formed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_turns_produce_valid_conversation(tmp_path: Any) -> None:
    """Two concurrent run_turn() calls must not corrupt conversation history."""
    agent = _make_agent(tmp_path)
    call_count = 0

    async def delayed_response(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0)  # yield to allow interleaving without lock
        return _mock_text_response(f"response {call_count}")

    with patch.object(agent._client.messages, "create", new=delayed_response):
        results = await asyncio.gather(
            agent.run_turn("turn one"),
            agent.run_turn("turn two"),
        )

    assert len(results) == 2
    assert all(r.startswith("response") for r in results)

    # Conversation must have alternating user/assistant pairs
    roles = [m["role"] for m in agent._conversation]
    assert len(roles) == 4  # 2 user + 2 assistant
    for i, expected in enumerate(["user", "assistant", "user", "assistant"]):
        assert roles[i] == expected, f"position {i}: expected {expected}, got {roles[i]}"
