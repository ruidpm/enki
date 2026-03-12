"""Tests for ManageTeamTool — create/update/deactivate with confirmation gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.teams.store import TeamsStore
from src.tools.manage_team import ManageTeamTool


@pytest.fixture
def store(tmp_path: Path) -> TeamsStore:
    return TeamsStore(tmp_path / "teams.db")


@pytest.fixture
def tool(store: TeamsStore) -> ManageTeamTool:
    return ManageTeamTool(store=store)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_team(tool: ManageTeamTool, store: TeamsStore) -> None:
    result = await tool.execute(
        action="create",
        team_id="researcher",
        name="Research Team",
        role="You are a research specialist.",
        tools=["web_search"],
        monthly_token_budget=50_000,
    )
    assert "created" in result.lower()
    team = store.get_team("researcher")
    assert team is not None
    assert team["name"] == "Research Team"
    assert team["tools"] == ["web_search"]
    assert team["monthly_token_budget"] == 50_000


@pytest.mark.asyncio
async def test_create_requires_team_id(tool: ManageTeamTool) -> None:
    result = await tool.execute(action="create", name="X", role="r", tools=[])
    assert "required" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_create_duplicate_team_overwrites(tool: ManageTeamTool, store: TeamsStore) -> None:
    await tool.execute(action="create", team_id="t1", name="Old", role="old role", tools=[])
    await tool.execute(action="create", team_id="t1", name="New", role="new role", tools=["notes"])
    team = store.get_team("t1")
    assert team["name"] == "New"
    assert team["role"] == "new role"


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_team(tool: ManageTeamTool, store: TeamsStore) -> None:
    store.create_team("eng", "Engineering", "old role", ["notes"])
    result = await tool.execute(
        action="update",
        team_id="eng",
        role="You are a senior software engineer.",
        tools=["notes", "web_search"],
    )
    assert "updated" in result.lower()
    team = store.get_team("eng")
    assert team["role"] == "You are a senior software engineer."
    assert set(team["tools"]) == {"notes", "web_search"}


@pytest.mark.asyncio
async def test_update_unknown_team_returns_error(tool: ManageTeamTool) -> None:
    result = await tool.execute(action="update", team_id="ghost", role="r", tools=[])
    assert "not found" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_update_partial_fields(tool: ManageTeamTool, store: TeamsStore) -> None:
    """Update only the budget — role and tools stay unchanged."""
    store.create_team("pm", "PM", "original role", ["tasks"], monthly_token_budget=10_000)
    result = await tool.execute(action="update", team_id="pm", monthly_token_budget=20_000)
    assert "updated" in result.lower()
    team = store.get_team("pm")
    assert team["monthly_token_budget"] == 20_000
    assert team["role"] == "original role"
    assert team["tools"] == ["tasks"]


# ---------------------------------------------------------------------------
# deactivate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_team(tool: ManageTeamTool, store: TeamsStore) -> None:
    store.create_team("fired", "Fired Team", "role", [])
    result = await tool.execute(action="deactivate", team_id="fired")
    assert "deactivated" in result.lower()
    team = store.get_team("fired")
    assert team["active"] == 0


@pytest.mark.asyncio
async def test_deactivate_unknown_team_returns_error(tool: ManageTeamTool) -> None:
    result = await tool.execute(action="deactivate", team_id="ghost")
    assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# unknown action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_action_returns_error(tool: ManageTeamTool) -> None:
    result = await tool.execute(action="nuke", team_id="t1")
    assert "unknown action" in result.lower() or "error" in result.lower()
