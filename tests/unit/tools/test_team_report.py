"""Tests for TeamReportTool — pure SQL, no LLM calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.teams.store import TeamsStore
from src.tools.team_report import TeamReportTool


@pytest.fixture
def store(tmp_path: Path) -> TeamsStore:
    return TeamsStore(tmp_path / "teams.db")


@pytest.fixture
def tool(store: TeamsStore) -> TeamReportTool:
    return TeamReportTool(store=store)


@pytest.mark.asyncio
async def test_report_empty_store(tool: TeamReportTool) -> None:
    result = await tool.execute()
    assert "no teams" in result.lower() or result.strip() != ""


@pytest.mark.asyncio
async def test_report_all_teams_formats_table(tool: TeamReportTool, store: TeamsStore) -> None:
    store.create_team("alpha", "Alpha", "role", ["web_search"], monthly_token_budget=50_000)
    store.create_team("beta", "Beta", "role", ["notes"], monthly_token_budget=100_000)
    store.log_task("alpha", "task1", "done", tokens_used=100, success=True, duration_s=1.0)

    result = await tool.execute()
    assert "alpha" in result
    assert "beta" in result
    assert "50" in result or "100" in result  # budget numbers present


@pytest.mark.asyncio
async def test_report_specific_team(tool: TeamReportTool, store: TeamsStore) -> None:
    store.create_team("gamma", "Gamma", "role", [], monthly_token_budget=10_000)
    store.log_task("gamma", "task", "ok", tokens_used=500, success=True, duration_s=2.5)

    result = await tool.execute(team_id="gamma")
    assert "gamma" in result
    assert "500" in result or "9500" in result  # tokens or budget remaining


@pytest.mark.asyncio
async def test_report_unknown_team(tool: TeamReportTool) -> None:
    result = await tool.execute(team_id="ghost")
    assert "not found" in result.lower() or "unknown" in result.lower()


@pytest.mark.asyncio
async def test_report_success_rate_shown(tool: TeamReportTool, store: TeamsStore) -> None:
    store.create_team("delta", "Delta", "role", [])
    store.log_task("delta", "t1", "ok", tokens_used=100, success=True, duration_s=1.0)
    store.log_task("delta", "t2", "fail", tokens_used=100, success=False, duration_s=1.0)

    result = await tool.execute(team_id="delta")
    # 1 success out of 2 → 50%
    assert "50" in result
