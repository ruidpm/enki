"""Tests for RunClaudeCodeTool output delivery integration.

The gist/summary logic itself is tested in test_output_delivery.py.
These tests verify the tool correctly delegates to OutputDelivery.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models import ModelId
from src.tools.claude_code import RunClaudeCodeTool


@pytest.fixture
def notifier() -> MagicMock:
    n = MagicMock()
    n.send = AsyncMock()
    n.ask_single_confirm = AsyncMock(return_value=True)
    return n


@pytest.fixture
def tool(notifier: MagicMock, tmp_path: Path) -> RunClaudeCodeTool:
    return RunClaudeCodeTool(notifier=notifier, project_dir=tmp_path)


def test_tool_has_output_delivery(tool: RunClaudeCodeTool) -> None:
    """Tool should have an OutputDelivery instance."""
    from src.output_delivery import OutputDelivery

    assert isinstance(tool._output, OutputDelivery)


def test_client_wired_to_output_delivery(notifier: MagicMock, tmp_path: Path) -> None:
    """anthropic_client passed to constructor should propagate to OutputDelivery."""
    mock_client = MagicMock()
    tool = RunClaudeCodeTool(
        notifier=notifier,
        project_dir=tmp_path,
        anthropic_client=mock_client,
        summary_model=ModelId.HAIKU,
    )
    assert tool._output._client is mock_client
    assert tool._output._model == ModelId.HAIKU
