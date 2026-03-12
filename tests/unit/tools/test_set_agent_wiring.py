"""Tests for set_agent() wiring pattern (H-12).

Tools with set_agent() must work without an agent wired (graceful fallback),
but the fallback behavior should be clearly defined and tested.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.teams.store import TeamsStore
from src.tools.claude_code import RunClaudeCodeTool
from src.tools.spawn_team import SpawnTeamTool

# ---------------------------------------------------------------------------
# SpawnTeamTool: without agent, raw result sent directly via notifier
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
async def test_spawn_team_no_agent_sends_raw_result(
    teams_store: TeamsStore,
    tmp_path: Path,
) -> None:
    """Without set_agent(), spawn_team sends raw result via notifier (no summarization)."""
    config = MagicMock()
    config.haiku_model = "claude-haiku-4-5-20251001"
    config.anthropic_api_key = "test-key"
    notifier = AsyncMock()
    notifier.send = AsyncMock()

    tool = SpawnTeamTool(
        store=teams_store,
        config=config,
        tool_registry={},
        notifier=notifier,
    )
    # Deliberately NOT calling set_agent()

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
async def test_spawn_team_with_agent_routes_through_enki(
    teams_store: TeamsStore,
    tmp_path: Path,
) -> None:
    """With set_agent(), spawn_team routes through Enki for summarization."""
    config = MagicMock()
    config.haiku_model = "claude-haiku-4-5-20251001"
    config.anthropic_api_key = "test-key"
    notifier = AsyncMock()
    notifier.send = AsyncMock()

    tool = SpawnTeamTool(
        store=teams_store,
        config=config,
        tool_registry={},
        notifier=notifier,
    )

    agent = MagicMock()
    agent.run_turn = AsyncMock(return_value="Summarized by Enki")
    tool.set_agent(agent)

    team = teams_store.get_team("researcher")
    assert team is not None

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(return_value=("raw result", 100))
        MockRunner.return_value = instance

        await tool._run_background("job2", "researcher", team, {}, "research task")

    agent.run_turn.assert_awaited_once()
    notifier.send.assert_awaited_once()
    msg = notifier.send.call_args[0][0]
    assert "Summarized by Enki" in msg


# ---------------------------------------------------------------------------
# RunClaudeCodeTool: without agent, OutputDelivery sends raw output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_code_no_agent_sends_raw_output() -> None:
    """Without set_agent(), OutputDelivery sends truncated raw text."""
    notifier = MagicMock()
    notifier.send = AsyncMock()
    notifier.ask_single_confirm = AsyncMock(return_value=True)

    tool = RunClaudeCodeTool(
        notifier=notifier,
        project_dir=Path("/tmp/fake"),
    )
    # Deliberately NOT calling set_agent() — agent is None in OutputDelivery

    await tool._output.send_output("job1", "Short output from CCC.", prefix="[Job job1] Done:")

    notifier.send.assert_awaited_once()
    msg = notifier.send.call_args[0][0]
    assert "Short output" in msg
    assert "job1" in msg
