"""Tests for sub-agent cost aggregation to main cost_guard (C-04)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.guardrails.cost_guard import CostGuardHook
from src.models import ModelId
from src.sub_agent import SubAgentRunner


@pytest.fixture
def cost_guard() -> CostGuardHook:
    return CostGuardHook(
        max_tokens_per_session=5_000_000,
        max_daily_cost_usd=50.0,
        max_monthly_cost_usd=300.0,
        max_llm_calls_per_session=1000,
        max_autonomous_turns=10,
    )


def test_sub_agent_accepts_cost_callback() -> None:
    """SubAgentRunner should accept an on_cost callback for reporting to cost_guard."""
    config = MagicMock()
    config.anthropic_api_key = "test"

    cost_cb = MagicMock()
    runner = SubAgentRunner(
        config=config,
        tools={},
        model=ModelId.HAIKU,
        on_cost=cost_cb,
    )
    assert runner._on_cost is cost_cb


def test_spawn_team_wires_cost_callback() -> None:
    """SpawnTeamTool should pass cost recording callback to SubAgentRunner."""
    from src.teams.store import TeamsStore
    from src.tools.spawn_team import SpawnTeamTool

    store = MagicMock(spec=TeamsStore)
    config = MagicMock()
    config.anthropic_api_key = "test"
    config.haiku_model = ModelId.HAIKU
    notifier = MagicMock()
    notifier.send = AsyncMock()
    cost_guard = CostGuardHook(
        max_tokens_per_session=5_000_000,
        max_daily_cost_usd=50.0,
        max_monthly_cost_usd=300.0,
        max_llm_calls_per_session=1000,
        max_autonomous_turns=10,
    )

    tool = SpawnTeamTool(
        store=store,
        config=config,
        tool_registry={},
        notifier=notifier,
        cost_guard=cost_guard,
    )
    assert tool._cost_guard is cost_guard


def test_spawn_agent_wires_cost_callback() -> None:
    """SpawnAgentTool should pass cost recording callback to SubAgentRunner."""
    from src.tools.spawn_agent import SpawnAgentTool

    config = MagicMock()
    config.anthropic_api_key = "test"
    cost_guard = CostGuardHook(
        max_tokens_per_session=5_000_000,
        max_daily_cost_usd=50.0,
        max_monthly_cost_usd=300.0,
        max_llm_calls_per_session=1000,
        max_autonomous_turns=10,
    )

    tool = SpawnAgentTool(
        config=config,
        tool_registry={},
        cost_guard=cost_guard,
    )
    assert tool._cost_guard is cost_guard
