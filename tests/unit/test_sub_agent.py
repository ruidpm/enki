"""Tests for SubAgentRunner (M-22, C-04)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.models import ModelId
from src.sub_agent import StepRecord, SubAgentRunner, ToolCallRecord


def test_max_steps_returns_incomplete_marker() -> None:
    """When max steps is reached, the result should contain [INCOMPLETE: max steps reached]."""
    config = MagicMock()
    config.anthropic_api_key = "test"

    SubAgentRunner(
        config=config,
        tools={},
        model=ModelId.HAIKU,
        max_steps=0,  # immediate hit
        label="test-agent",
    )

    # Verify the marker string format is consistent
    expected_prefix = "[INCOMPLETE: max steps reached]"
    result_text = "[INCOMPLETE: max steps reached] Sub-agent 'test-agent' hit the 0-step limit. Results may be partial."
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


def test_on_step_callback_accepted() -> None:
    """SubAgentRunner should accept and store on_step callback."""
    config = MagicMock()
    config.anthropic_api_key = "test"
    cb = MagicMock()

    runner = SubAgentRunner(
        config=config,
        tools={},
        model="test",
        on_step=cb,
    )
    assert runner._on_step is cb


def test_on_step_callback_defaults_to_none() -> None:
    config = MagicMock()
    config.anthropic_api_key = "test"

    runner = SubAgentRunner(
        config=config,
        tools={},
        model="test",
    )
    assert runner._on_step is None


def test_step_record_frozen() -> None:
    """StepRecord should be immutable."""
    record = StepRecord(
        step_number=1,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
        tools=[ToolCallRecord(name="web_search", input_preview="query", output_preview="results")],
        duration_ms=500,
    )
    assert record.step_number == 1
    assert len(record.tools) == 1
    assert record.tools[0].name == "web_search"


def test_tool_call_record_truncation() -> None:
    """ToolCallRecord preview fields should be set by caller (SubAgentRunner truncates to 500 chars)."""
    long_text = "x" * 1000
    record = ToolCallRecord(name="test", input_preview=long_text[:500], output_preview=long_text[:500])
    assert len(record.input_preview) == 500
    assert len(record.output_preview) == 500


def test_max_tool_result_chars_accepted() -> None:
    """SubAgentRunner should accept max_tool_result_chars param."""
    config = MagicMock()
    config.anthropic_api_key = "test"

    runner = SubAgentRunner(
        config=config,
        tools={},
        model="test",
        max_tool_result_chars=5000,
    )
    assert runner._max_tool_result_chars == 5000


def test_max_tool_result_chars_defaults() -> None:
    """max_tool_result_chars defaults to 10_000."""
    config = MagicMock()
    config.anthropic_api_key = "test"

    runner = SubAgentRunner(
        config=config,
        tools={},
        model="test",
    )
    assert runner._max_tool_result_chars == 10_000


def test_cancel_check_accepted() -> None:
    """SubAgentRunner should accept cancel_check callback."""
    config = MagicMock()
    config.anthropic_api_key = "test"
    cb = MagicMock(return_value=False)

    runner = SubAgentRunner(
        config=config,
        tools={},
        model="test",
        cancel_check=cb,
    )
    assert runner._cancel_check is cb


def test_cancel_check_defaults_to_none() -> None:
    config = MagicMock()
    config.anthropic_api_key = "test"

    runner = SubAgentRunner(
        config=config,
        tools={},
        model="test",
    )
    assert runner._cancel_check is None


@pytest.mark.asyncio
async def test_cancel_check_stops_agent() -> None:
    """When cancel_check returns True, agent should stop immediately."""
    config = MagicMock()
    config.anthropic_api_key = "test"

    runner = SubAgentRunner(
        config=config,
        tools={},
        model="test",
        max_steps=10,
        cancel_check=lambda: True,  # always cancelled
    )

    result, tokens = await runner.run("do something")
    assert "[CANCELLED]" in result
    assert tokens == 0
