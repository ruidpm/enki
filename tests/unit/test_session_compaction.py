"""Tests for compaction on session reset — facts should not be lost on idle timeout."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestSessionResetCompaction:
    """Agent.new_session() should trigger compaction for the old session."""

    @pytest.mark.asyncio
    async def test_new_session_triggers_compaction(self) -> None:
        from src.agent import Agent

        config = MagicMock()
        config.default_model = "claude-sonnet"
        config.haiku_model = "claude-haiku"
        config.opus_model = "claude-opus"
        config.anthropic_api_key = "test-key"
        config.max_autonomous_turns = 5
        config.session_timeout_hours = 24
        config.max_context_tokens = 120_000

        compactor = AsyncMock()
        compactor.compact_session = AsyncMock(return_value=[])

        agent = Agent(
            config=config,
            guardrails=MagicMock(),
            memory=MagicMock(build_context=MagicMock(return_value=""), append_turn=MagicMock()),
            tool_registry={},
            audit=MagicMock(log_tier2=AsyncMock(), log_tool_call=AsyncMock()),
            cost_guard=MagicMock(
                daily_cost_usd=0.0,
                monthly_cost_usd=0.0,
                session_tokens=0,
                record_llm_call=MagicMock(),
                record_autonomous_turn=MagicMock(),
                on_user_message=MagicMock(),
                reset_session=MagicMock(),
            ),
            loop_detector=MagicMock(set_session=MagicMock(), on_user_message=MagicMock()),
            rate_limiter=MagicMock(reset=MagicMock()),
        )

        old_session_id = agent.session_id
        agent.set_compactor(compactor)
        agent.new_session()

        # Give the background task a chance to run
        await asyncio.sleep(0.1)

        compactor.compact_session.assert_called_once_with(old_session_id)

    @pytest.mark.asyncio
    async def test_new_session_without_compactor_does_not_crash(self) -> None:
        from src.agent import Agent

        config = MagicMock()
        config.default_model = "claude-sonnet"
        config.haiku_model = "claude-haiku"
        config.opus_model = "claude-opus"
        config.anthropic_api_key = "test-key"
        config.max_autonomous_turns = 5
        config.session_timeout_hours = 24
        config.max_context_tokens = 120_000

        agent = Agent(
            config=config,
            guardrails=MagicMock(),
            memory=MagicMock(build_context=MagicMock(return_value=""), append_turn=MagicMock()),
            tool_registry={},
            audit=MagicMock(log_tier2=AsyncMock(), log_tool_call=AsyncMock()),
            cost_guard=MagicMock(
                daily_cost_usd=0.0,
                monthly_cost_usd=0.0,
                session_tokens=0,
                record_llm_call=MagicMock(),
                record_autonomous_turn=MagicMock(),
                on_user_message=MagicMock(),
                reset_session=MagicMock(),
            ),
            loop_detector=MagicMock(set_session=MagicMock(), on_user_message=MagicMock()),
            rate_limiter=MagicMock(reset=MagicMock()),
        )

        # No compactor set — should not crash
        agent.new_session()
