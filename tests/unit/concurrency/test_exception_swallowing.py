"""Tests for C-07: Background task exception swallowing — verify fallback paths are safe."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.teams.store import TeamsStore
from src.tools.claude_code import RunClaudeCodeTool
from src.tools.spawn_team import SpawnTeamTool

# ---------------------------------------------------------------------------
# SpawnTeamTool — fallback notifier.send() failure must be caught
# ---------------------------------------------------------------------------


@pytest.fixture
def teams_store(tmp_path: Path) -> TeamsStore:
    s = TeamsStore(tmp_path / "teams.db")
    s.create_team("t1", "Team 1", "role", ["web_search"], monthly_token_budget=50_000)
    return s


@pytest.fixture
def config() -> MagicMock:
    c = MagicMock()
    c.haiku_model = "claude-haiku-4-5-20251001"
    c.anthropic_api_key = "test-key"
    return c


@pytest.mark.asyncio
async def test_spawn_team_sage_relay_error_with_fallback_failure(
    teams_store: TeamsStore, config: MagicMock
) -> None:
    """When agent.run_turn raises AND fallback notifier.send raises, no exception escapes."""
    notifier = AsyncMock()
    # First call (inside except block, fallback send) also fails
    notifier.send = AsyncMock(side_effect=RuntimeError("notification service down"))

    agent = AsyncMock()
    agent.run_turn = AsyncMock(side_effect=RuntimeError("agent error"))

    tool = SpawnTeamTool(
        store=teams_store,
        config=config,
        tool_registry={},
        notifier=notifier,
    )
    tool.set_agent(agent)

    team = teams_store.get_team("t1")
    assert team is not None

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(return_value=("result", 100))
        MockRunner.return_value = instance

        # This must NOT raise — exception must be caught and logged
        await tool._run_background("j1", "t1", team, {}, "task")


@pytest.mark.asyncio
async def test_spawn_team_no_agent_fallback_send_failure(
    teams_store: TeamsStore, config: MagicMock
) -> None:
    """When agent is None and fallback notifier.send raises, no exception escapes."""
    notifier = AsyncMock()
    notifier.send = AsyncMock(side_effect=RuntimeError("send failed"))

    tool = SpawnTeamTool(
        store=teams_store,
        config=config,
        tool_registry={},
        notifier=notifier,
    )
    # No agent wired

    team = teams_store.get_team("t1")
    assert team is not None

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(return_value=("result", 100))
        MockRunner.return_value = instance

        # This must NOT raise
        await tool._run_background("j2", "t1", team, {}, "task")


# ---------------------------------------------------------------------------
# RunClaudeCodeTool — _send_output fallback failure must be caught
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_code_send_output_agent_summary_fails_and_notifier_fails(
    tmp_path: Path,
) -> None:
    """When agent.run_turn fails and notifier.send also fails, no exception escapes."""
    notifier = MagicMock()
    notifier.ask_single_confirm = AsyncMock(return_value=True)
    notifier.send = AsyncMock(side_effect=RuntimeError("telegram down"))

    tool = RunClaudeCodeTool(notifier=notifier, project_dir=tmp_path)
    agent = AsyncMock()
    agent.run_turn = AsyncMock(side_effect=RuntimeError("agent crashed"))
    tool.set_agent(agent)

    # This must NOT raise
    await tool._send_output("job1", "x" * 1000)


@pytest.mark.asyncio
async def test_claude_code_send_output_short_notifier_fails(
    tmp_path: Path,
) -> None:
    """When output is short and notifier.send fails, no exception escapes."""
    notifier = MagicMock()
    notifier.send = AsyncMock(side_effect=RuntimeError("telegram down"))

    tool = RunClaudeCodeTool(notifier=notifier, project_dir=tmp_path)

    # This must NOT raise
    await tool._send_output("job2", "short output")
