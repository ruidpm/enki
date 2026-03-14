"""Tests for context reinforcement — anti-drift reminders in long agentic loops."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import Agent
from src.models import ModelId

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> MagicMock:
    cfg = MagicMock()
    cfg.anthropic_api_key = "sk-test"
    cfg.haiku_model = ModelId.HAIKU
    cfg.default_model = ModelId.SONNET
    cfg.opus_model = ModelId.OPUS
    cfg.max_context_tokens = 120_000
    cfg.session_timeout_hours = 8.0
    cfg.max_autonomous_turns = 10
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_agent(config: MagicMock | None = None) -> Agent:
    cfg = config or _make_config()
    guardrails = MagicMock()
    memory = MagicMock()
    audit = AsyncMock()
    cost_guard = MagicMock()
    cost_guard.daily_cost_usd = 0.0
    cost_guard.monthly_cost_usd = 0.0
    cost_guard.session_tokens = 0
    loop_detector = MagicMock()
    rate_limiter = MagicMock()

    with patch("src.agent.anthropic.AsyncAnthropic"):
        agent = Agent(
            config=cfg,
            guardrails=guardrails,
            memory=memory,
            tool_registry={},
            audit=audit,
            cost_guard=cost_guard,
            loop_detector=loop_detector,
            rate_limiter=rate_limiter,
        )
    return agent


# ===================================================================
# _context_reinforcement unit tests
# ===================================================================


class TestContextReinforcement:
    def test_no_reinforcement_on_early_turns(self) -> None:
        """Turns 0 and 1 should return None — no reminder needed."""
        agent = _make_agent()
        assert agent._context_reinforcement(0, "hello", ["tasks"]) is None
        assert agent._context_reinforcement(1, "hello", ["tasks"]) is None

    def test_reinforcement_on_turn_2_plus(self) -> None:
        """Turn 2+ should return a dict with type=text and the reminder."""
        agent = _make_agent()
        result = agent._context_reinforcement(2, "find all bugs", ["web_search", "tasks"])
        assert result is not None
        assert result["type"] == "text"
        assert "Autonomous turn 3" in result["text"]
        assert "find all bugs" in result["text"]
        assert "Stay focused" in result["text"]

        # Turn 5 also works
        result5 = agent._context_reinforcement(5, "deploy app", ["git_push_branch"])
        assert result5 is not None
        assert "Autonomous turn 6" in result5["text"]

    def test_user_summary_truncated(self) -> None:
        """User messages longer than 200 chars are truncated in the reminder."""
        agent = _make_agent()
        long_msg = "x" * 300
        result = agent._context_reinforcement(2, long_msg, ["tasks"])
        assert result is not None
        # The original 300-char message should be truncated to 200
        assert "x" * 200 in result["text"]
        assert "x" * 201 not in result["text"]

    def test_tools_listed(self) -> None:
        """Tool names should appear in the reminder text."""
        agent = _make_agent()
        tools = ["web_search", "tasks", "calendar_read"]
        result = agent._context_reinforcement(3, "research topic", tools)
        assert result is not None
        for tool in tools:
            assert tool in result["text"]


# ===================================================================
# Integration: verify reinforcement is injected into the agent loop
# ===================================================================


class TestReinforcementInjectedInLoop:
    @pytest.mark.asyncio
    async def test_reinforcement_injected_in_agent_loop(self) -> None:
        """After 3+ tool-use turns, a context text block appears in conversation."""
        agent = _make_agent()

        # Set up guardrails to allow everything
        agent._guardrails.run = AsyncMock(return_value=(True, None))

        # Create a fake tool
        fake_tool = MagicMock()
        fake_tool.name = "fake_tool"
        fake_tool.description = "fake"
        fake_tool.input_schema = {"type": "object", "properties": {}}
        fake_tool.execute = AsyncMock(return_value="done")
        agent._tools = {"fake_tool": fake_tool}

        # Build a sequence: 3 tool-use responses, then a text response
        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.name = "fake_tool"
        tool_use_block.input = {}
        tool_use_block.id = "tu_1"

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "All done!"

        def _make_response(stop_reason: str, content: list[Any]) -> MagicMock:
            resp = MagicMock()
            resp.stop_reason = stop_reason
            resp.content = content
            resp.usage = MagicMock()
            resp.usage.input_tokens = 100
            resp.usage.output_tokens = 50
            resp.usage.cache_creation_input_tokens = 0
            resp.usage.cache_read_input_tokens = 0
            return resp

        # 3 tool-use turns, then a final text turn
        responses = [
            _make_response("tool_use", [tool_use_block]),
            _make_response("tool_use", [tool_use_block]),
            _make_response("tool_use", [tool_use_block]),
            _make_response("end_turn", [text_block]),
        ]

        agent._api_call_with_retry = AsyncMock(side_effect=responses)

        result = await agent.run_turn("Do a multi-step task for me")
        assert result == "All done!"

        # Inspect the conversation: find user messages with tool_results
        tool_result_msgs = [msg for msg in agent._conversation if msg["role"] == "user" and isinstance(msg["content"], list)]

        # Should have 3 tool-result messages (turns 0, 1, 2)
        assert len(tool_result_msgs) == 3

        # Turn 0 and 1 (index 0, 1) should NOT have a text block
        for msg in tool_result_msgs[:2]:
            text_blocks = [b for b in msg["content"] if isinstance(b, dict) and b.get("type") == "text"]
            assert len(text_blocks) == 0, "Early turns should not have reinforcement"

        # Turn 2 (index 2) SHOULD have a text block with context reminder
        last_tool_msg = tool_result_msgs[2]
        text_blocks = [b for b in last_tool_msg["content"] if isinstance(b, dict) and b.get("type") == "text"]
        assert len(text_blocks) == 1, "Turn 2 should have reinforcement"
        assert "Autonomous turn 3" in text_blocks[0]["text"]
        assert "Do a multi-step task for me" in text_blocks[0]["text"]
        assert "fake_tool" in text_blocks[0]["text"]
