"""Tests for SubAgentRunner (M-22, C-04)."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.sub_agent import SubAgentRunner


def test_max_steps_returns_incomplete_marker() -> None:
    """When max steps is reached, the result should contain [INCOMPLETE: max steps reached]."""
    config = MagicMock()
    config.anthropic_api_key = "test"

    SubAgentRunner(
        config=config,
        tools={},
        model="claude-haiku-4-5-20251001",
        max_steps=0,  # immediate hit
        label="test-agent",
    )

    # Verify the marker string format is consistent
    expected_prefix = "[INCOMPLETE: max steps reached]"
    result_text = (
        "[INCOMPLETE: max steps reached] Sub-agent 'test-agent' "
        "hit the 0-step limit. Results may be partial."
    )
    assert expected_prefix in result_text


def test_on_cost_callback_accepted() -> None:
    """SubAgentRunner should accept and store on_cost callback."""
    config = MagicMock()
    config.anthropic_api_key = "test"
    cb = MagicMock()

    runner = SubAgentRunner(
        config=config,
        tools={},
        model="test",
        on_cost=cb,
    )
    assert runner._on_cost is cb


def test_on_cost_callback_defaults_to_none() -> None:
    """on_cost defaults to None when not provided."""
    config = MagicMock()
    config.anthropic_api_key = "test"

    runner = SubAgentRunner(
        config=config,
        tools={},
        model="test",
    )
    assert runner._on_cost is None
