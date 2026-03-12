"""Unit tests for Agent core helpers: _estimate_tokens, _model_for_tier, new_session, classify_complexity."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import Agent, ModelTier, classify_complexity
from src.models import ModelId

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> MagicMock:
    """Minimal Settings mock with model defaults."""
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
    """Build an Agent with all deps mocked out."""
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
# _estimate_tokens
# ===================================================================


class TestEstimateTokens:
    def test_empty_conversation_returns_zero(self) -> None:
        agent = _make_agent()
        assert agent._estimate_tokens() == 0

    def test_text_messages(self) -> None:
        agent = _make_agent()
        agent._conversation = [
            {"role": "user", "content": "a" * 400},
            {"role": "assistant", "content": "b" * 400},
        ]
        # 800 chars / 4 == 200
        assert agent._estimate_tokens() == 200

    def test_none_content_does_not_crash(self) -> None:
        agent = _make_agent()
        agent._conversation = [
            {"role": "user", "content": None},
            {"role": "assistant", "content": ""},
            {"role": "user"},  # missing "content" key entirely
        ]
        # Should not raise
        result = agent._estimate_tokens()
        assert isinstance(result, int)

    def test_list_content_blocks(self) -> None:
        agent = _make_agent()
        agent._conversation = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "x" * 120},
                    {"type": "tool_use", "content": "y" * 80},
                ],
            }
        ]
        # chars from text + content keys processed
        tokens = agent._estimate_tokens()
        assert tokens > 0


# ===================================================================
# _model_for_tier
# ===================================================================


class TestModelForTier:
    def test_haiku_tier(self) -> None:
        agent = _make_agent()
        assert agent._model_for_tier(ModelTier.HAIKU) == ModelId.HAIKU

    def test_sonnet_tier(self) -> None:
        agent = _make_agent()
        assert agent._model_for_tier(ModelTier.SONNET) == ModelId.SONNET

    def test_opus_tier(self) -> None:
        agent = _make_agent()
        assert agent._model_for_tier(ModelTier.OPUS) == ModelId.OPUS


# ===================================================================
# new_session
# ===================================================================


class TestNewSession:
    def test_resets_conversation_and_session_id(self) -> None:
        agent = _make_agent()
        old_id = agent.session_id
        agent._conversation.append({"role": "user", "content": "hi"})

        agent.new_session()

        assert agent._conversation == []
        assert agent.session_id != old_id
        agent._loop_detector.set_session.assert_called()
        agent._cost_guard.reset_session.assert_called_once()

    def test_without_compactor_no_error(self) -> None:
        agent = _make_agent()
        assert agent._compactor is None
        # Should not raise
        agent.new_session()

    @pytest.mark.asyncio
    async def test_with_compactor_creates_background_task(self) -> None:
        agent = _make_agent()
        compactor = AsyncMock()
        agent.set_compactor(compactor)
        old_id = agent.session_id

        # new_session creates a background task — we need an event loop
        agent.new_session()

        # Let the background task run
        await asyncio.sleep(0.05)

        compactor.compact_session.assert_awaited_once_with(old_id)


# ===================================================================
# classify_complexity — only add cases NOT already covered in test_model_routing.py
# ===================================================================


class TestClassifyComplexity:
    """Additional classify_complexity tests beyond test_model_routing.py."""

    def test_architect_keyword_routes_to_opus(self) -> None:
        # test_model_routing already tests "architect a full migration plan..."
        # This tests the bare keyword
        assert classify_complexity("architect this") == ModelTier.OPUS

    def test_list_keyword_routes_to_haiku(self) -> None:
        # test_model_routing tests "list my tasks" — this tests another pattern
        assert classify_complexity("list all files") == ModelTier.HAIKU

    def test_generic_message_routes_to_sonnet(self) -> None:
        assert classify_complexity("write me a poem about cats") == ModelTier.SONNET

    def test_comprehensive_keyword_routes_to_opus(self) -> None:
        assert classify_complexity("give me a comprehensive overview") == ModelTier.OPUS

    def test_design_system_routes_to_opus(self) -> None:
        assert classify_complexity("design system for auth") == ModelTier.OPUS

    def test_summarize_briefly_routes_to_haiku(self) -> None:
        assert classify_complexity("summarize briefly the meeting notes") == ModelTier.HAIKU
