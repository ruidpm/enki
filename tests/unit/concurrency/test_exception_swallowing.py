"""Tests for C-07: Background task exception swallowing — verify fallback paths are safe."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import ModelId
from src.teams.store import TeamsStore
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
    c.haiku_model = ModelId.HAIKU
    c.anthropic_api_key = "test-key"
    return c


@pytest.mark.asyncio
async def test_spawn_team_summary_error_with_fallback_failure(teams_store: TeamsStore, config: MagicMock) -> None:
    """When stateless API raises AND fallback notifier.send raises, no exception escapes."""
    notifier = AsyncMock()
    # First call (inside except block, fallback send) also fails
    notifier.send = AsyncMock(side_effect=RuntimeError("notification service down"))

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API error"))

    tool = SpawnTeamTool(
        store=teams_store,
        config=config,
        tool_registry={},
        notifier=notifier,
        anthropic_client=mock_client,
        summary_model=ModelId.HAIKU,
    )

    team = teams_store.get_team("t1")
    assert team is not None

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(return_value=("result", 100))
        MockRunner.return_value = instance

        # This must NOT raise — exception must be caught and logged
        await tool._run_background("j1", "t1", team, {}, "task")


@pytest.mark.asyncio
async def test_spawn_team_no_client_fallback_send_failure(teams_store: TeamsStore, config: MagicMock) -> None:
    """When client is None and fallback notifier.send raises, no exception escapes."""
    notifier = AsyncMock()
    notifier.send = AsyncMock(side_effect=RuntimeError("send failed"))

    tool = SpawnTeamTool(
        store=teams_store,
        config=config,
        tool_registry={},
        notifier=notifier,
        # No anthropic_client
    )

    team = teams_store.get_team("t1")
    assert team is not None

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(return_value=("result", 100))
        MockRunner.return_value = instance

        # This must NOT raise
        await tool._run_background("j2", "t1", team, {}, "task")


# ---------------------------------------------------------------------------
# OutputDelivery — send_output fallback failure must be caught
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_delivery_client_and_notifier_both_fail() -> None:
    """When stateless API fails and notifier.send also fails, no exception escapes."""
    from src.output_delivery import OutputDelivery

    notifier = MagicMock()
    notifier.send = AsyncMock(side_effect=RuntimeError("telegram down"))

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API crashed"))

    delivery = OutputDelivery(
        notifier=notifier,
        anthropic_client=mock_client,
        model=ModelId.HAIKU,
    )

    # This must NOT raise
    await delivery.send_output("job1", "x" * 1000)


@pytest.mark.asyncio
async def test_output_delivery_short_notifier_fails() -> None:
    """When output is short and notifier.send fails, no exception escapes."""
    from src.output_delivery import OutputDelivery

    notifier = MagicMock()
    notifier.send = AsyncMock(side_effect=RuntimeError("telegram down"))

    delivery = OutputDelivery(notifier=notifier)

    # This must NOT raise
    await delivery.send_output("job2", "short output")
