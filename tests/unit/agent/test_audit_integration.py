"""Tests for audit integration in agent — H-02, L-14."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.audit.db import AuditDB
from src.config import Settings
from src.guardrails import GuardrailChain
from src.guardrails.allowlist import AllowlistHook
from src.guardrails.cost_guard import CostGuardHook
from src.guardrails.loop_detector import LoopDetectorHook
from src.guardrails.rate_limiter import RateLimiterHook


class FakeTool:
    name = "fake_tool"
    description = "a test tool"
    input_schema: dict[str, Any] = {"type": "object", "properties": {"q": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        return "tool result here"


class BlockedFakeTool:
    """Not registered — will be blocked by allowlist."""
    name = "blocked_tool"
    description = "blocked"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "should not run"


def _make_agent(
    tmp_path: Any,
    tool_registry: dict[str, Any] | None = None,
) -> Any:
    """Build a minimal Agent with mocked Anthropic client."""
    from src.agent import Agent

    fake = FakeTool()
    reg = tool_registry if tool_registry is not None else {fake.name: fake}

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

    chain = GuardrailChain([
        AllowlistHook(reg),
        loop_detector,
        rate_limiter,
        cost_guard,
    ])

    agent = Agent(
        config=config,
        guardrails=chain,
        memory=memory,
        tool_registry=reg,
        audit=audit,
        cost_guard=cost_guard,
        loop_detector=loop_detector,
        rate_limiter=rate_limiter,
    )
    return agent, audit


@pytest.mark.asyncio
async def test_audit_log_tool_call_called_for_allowed(tmp_path: Any) -> None:
    """After guardrails allow a tool, audit.log_tool_call must be called with params."""
    agent, audit = _make_agent(tmp_path)

    # Spy on log_tool_call
    original = audit.log_tool_call
    calls: list[dict[str, Any]] = []

    async def spy(**kwargs: Any) -> None:
        calls.append(kwargs)
        await original(**kwargs)

    audit.log_tool_call = spy  # type: ignore[assignment]

    # Mock the Anthropic API to return a tool_use then a text response
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "fake_tool"
    tool_use_block.input = {"q": "hello"}
    tool_use_block.id = "tu_1"

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Done!"

    response_with_tool = MagicMock()
    response_with_tool.stop_reason = "tool_use"
    response_with_tool.content = [tool_use_block]
    response_with_tool.usage = MagicMock(input_tokens=100, output_tokens=50)

    response_text = MagicMock()
    response_text.stop_reason = "end_turn"
    response_text.content = [text_block]
    response_text.usage = MagicMock(input_tokens=200, output_tokens=100)

    agent._client.messages.create = AsyncMock(
        side_effect=[response_with_tool, response_text]
    )

    await agent.run_turn("test")

    # Verify log_tool_call was called for allowed tool with params
    assert len(calls) >= 1
    call = calls[0]
    assert call["tool_name"] == "fake_tool"
    assert call["params"] == {"q": "hello"}
    assert call["allowed"] is True
    assert call["block_reason"] is None


@pytest.mark.asyncio
async def test_audit_log_tool_call_called_for_blocked(tmp_path: Any) -> None:
    """Blocked tool calls must also be recorded via audit.log_tool_call with params."""
    agent, audit = _make_agent(tmp_path)

    calls: list[dict[str, Any]] = []
    original = audit.log_tool_call

    async def spy(**kwargs: Any) -> None:
        calls.append(kwargs)
        await original(**kwargs)

    audit.log_tool_call = spy  # type: ignore[assignment]

    # Model tries to call an unregistered tool
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "unregistered_tool"
    tool_use_block.input = {"action": "delete"}
    tool_use_block.id = "tu_2"

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Sorry"

    response_with_tool = MagicMock()
    response_with_tool.stop_reason = "tool_use"
    response_with_tool.content = [tool_use_block]
    response_with_tool.usage = MagicMock(input_tokens=100, output_tokens=50)

    response_text = MagicMock()
    response_text.stop_reason = "end_turn"
    response_text.content = [text_block]
    response_text.usage = MagicMock(input_tokens=200, output_tokens=100)

    agent._client.messages.create = AsyncMock(
        side_effect=[response_with_tool, response_text]
    )

    await agent.run_turn("test")

    # Verify log_tool_call was called for blocked tool
    assert len(calls) >= 1
    call = calls[0]
    assert call["tool_name"] == "unregistered_tool"
    assert call["params"] == {"action": "delete"}
    assert call["allowed"] is False


@pytest.mark.asyncio
async def test_tier2_tool_log_includes_result_truncated(tmp_path: Any) -> None:
    """L-14: Tier2 TOOL_CALLED log must include first 200 chars of result."""
    agent, audit = _make_agent(tmp_path)

    tier2_calls: list[dict[str, Any]] = []
    original_tier2 = audit.log_tier2

    async def spy_tier2(event_type: Any, session_id: str, data: dict[str, Any]) -> None:
        tier2_calls.append({"event_type": event_type, "data": data})
        await original_tier2(event_type, session_id, data)

    audit.log_tier2 = spy_tier2  # type: ignore[assignment]

    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "fake_tool"
    tool_use_block.input = {"q": "hello"}
    tool_use_block.id = "tu_3"

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Done!"

    response_with_tool = MagicMock()
    response_with_tool.stop_reason = "tool_use"
    response_with_tool.content = [tool_use_block]
    response_with_tool.usage = MagicMock(input_tokens=100, output_tokens=50)

    response_text = MagicMock()
    response_text.stop_reason = "end_turn"
    response_text.content = [text_block]
    response_text.usage = MagicMock(input_tokens=200, output_tokens=100)

    agent._client.messages.create = AsyncMock(
        side_effect=[response_with_tool, response_text]
    )

    await agent.run_turn("test")

    # Find the TOOL_CALLED tier2 entry
    tool_result_logs = [
        c for c in tier2_calls
        if c["data"].get("tool") == "fake_tool" and "result_preview" in c["data"]
    ]
    assert len(tool_result_logs) >= 1
    data = tool_result_logs[0]["data"]
    assert data["result_preview"] == "tool result here"
