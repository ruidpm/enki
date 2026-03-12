"""Tests for stateless summarization wiring pattern.

Tools use a shared Anthropic client for stateless summarization instead of
agent.run_turn() (which would pollute the main conversation history).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import ModelId
from src.teams.store import TeamsStore
from src.tools.claude_code import RunClaudeCodeTool
from src.tools.spawn_team import SpawnTeamTool

# ---------------------------------------------------------------------------
# SpawnTeamTool: without client, raw result sent directly via notifier
# ---------------------------------------------------------------------------


@pytest.fixture
def teams_store(tmp_path: Path) -> TeamsStore:
    store = TeamsStore(tmp_path / "teams.db")
    store.create_team(
        team_id="researcher",
        name="Researcher",
        role="You research.",
        tools=["web_search"],
        monthly_token_budget=50_000,
    )
    return store


@pytest.mark.asyncio
async def test_spawn_team_no_client_sends_raw_result(
    teams_store: TeamsStore,
    tmp_path: Path,
) -> None:
    """Without anthropic_client, spawn_team sends raw result via notifier."""
    config = MagicMock()
    config.haiku_model = ModelId.HAIKU
    config.anthropic_api_key = "test-key"
    notifier = AsyncMock()
    notifier.send = AsyncMock()

    tool = SpawnTeamTool(
        store=teams_store,
        config=config,
        tool_registry={},
        notifier=notifier,
        # No anthropic_client — stateless summarization unavailable
    )

    team = teams_store.get_team("researcher")
    assert team is not None

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(return_value=("raw research result", 100))
        MockRunner.return_value = instance

        await tool._run_background("job1", "researcher", team, {}, "do research")

    notifier.send.assert_awaited_once()
    msg = notifier.send.call_args[0][0]
    assert "raw research result" in msg


@pytest.mark.asyncio
async def test_spawn_team_with_client_uses_stateless_summary(
    teams_store: TeamsStore,
    tmp_path: Path,
) -> None:
    """With anthropic_client, spawn_team summarizes via stateless API call."""
    config = MagicMock()
    config.haiku_model = ModelId.HAIKU
    config.anthropic_api_key = "test-key"
    notifier = AsyncMock()
    notifier.send = AsyncMock()

    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.content = [AsyncMock(text="Summarized by stateless API")]
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    tool = SpawnTeamTool(
        store=teams_store,
        config=config,
        tool_registry={},
        notifier=notifier,
        anthropic_client=mock_client,
        summary_model=ModelId.HAIKU,
    )

    team = teams_store.get_team("researcher")
    assert team is not None

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(return_value=("raw result", 100))
        MockRunner.return_value = instance

        await tool._run_background("job2", "researcher", team, {}, "research task")

    mock_client.messages.create.assert_awaited_once()
    notifier.send.assert_awaited_once()
    msg = notifier.send.call_args[0][0]
    assert "Summarized by stateless API" in msg


# ---------------------------------------------------------------------------
# RunClaudeCodeTool: without client, OutputDelivery sends raw output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_code_no_client_sends_raw_output() -> None:
    """Without anthropic_client, OutputDelivery sends truncated raw text."""
    notifier = MagicMock()
    notifier.send = AsyncMock()
    notifier.ask_single_confirm = AsyncMock(return_value=True)

    tool = RunClaudeCodeTool(
        notifier=notifier,
        project_dir=Path("/tmp/fake"),
        # No anthropic_client
    )

    await tool._output.send_output("job1", "Short output from CCC.", prefix="[Job job1] Done:")

    notifier.send.assert_awaited_once()
    msg = notifier.send.call_args[0][0]
    assert "Short output" in msg
    assert "job1" in msg
