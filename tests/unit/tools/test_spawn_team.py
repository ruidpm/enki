"""Tests for SpawnTeamTool — fire-and-forget background delegation."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.teams.store import TeamsStore
from src.tools.spawn_team import SpawnTeamTool


@pytest.fixture
def store(tmp_path: Path) -> TeamsStore:
    s = TeamsStore(tmp_path / "teams.db")
    s.create_team(
        team_id="researcher",
        name="Research Team",
        role="You are a research specialist.",
        tools=["web_search"],
        monthly_token_budget=50_000,
    )
    return s


@pytest.fixture
def config() -> MagicMock:
    c = MagicMock()
    c.haiku_model = "claude-haiku-4-5-20251001"
    c.anthropic_api_key = "test-key"
    return c


@pytest.fixture
def notifier() -> AsyncMock:
    n = AsyncMock()
    n.send = AsyncMock()
    return n


@pytest.fixture
def tool_registry() -> dict:
    mock_tool = MagicMock()
    mock_tool.name = "web_search"
    mock_tool.description = "Search the web"
    mock_tool.input_schema = {"type": "object", "properties": {}}
    return {"web_search": mock_tool}


@pytest.fixture
def tool(store: TeamsStore, config: MagicMock, tool_registry: dict, notifier: AsyncMock) -> SpawnTeamTool:
    return SpawnTeamTool(store=store, config=config, tool_registry=tool_registry, notifier=notifier)


# ---------------------------------------------------------------------------
# Gate checks (synchronous, before background task fires)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_team_returns_error(tool: SpawnTeamTool) -> None:
    result = await tool.execute(team_id="nonexistent", task="do something")
    assert "not found" in result.lower() or "unknown" in result.lower()


@pytest.mark.asyncio
async def test_inactive_team_returns_error(tool: SpawnTeamTool, store: TeamsStore) -> None:
    store.deactivate_team("researcher")
    result = await tool.execute(team_id="researcher", task="do something")
    assert "not found" in result.lower() or "inactive" in result.lower()


@pytest.mark.asyncio
async def test_budget_exceeded_returns_blocked(tool: SpawnTeamTool, store: TeamsStore) -> None:
    store.log_task("researcher", "big task", "ok", tokens_used=50_001, success=True, duration_s=1.0)
    result = await tool.execute(team_id="researcher", task="another task")
    assert "budget" in result.lower() or "blocked" in result.lower()


# ---------------------------------------------------------------------------
# Non-blocking: execute() must return immediately
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_returns_immediately_with_job_id(tool: SpawnTeamTool) -> None:
    with patch.object(tool, "_run_background", new=AsyncMock()):
        result = await tool.execute(team_id="researcher", task="find AI news")
        await asyncio.sleep(0)
    assert "job" in result.lower() or "delegated" in result.lower()


# ---------------------------------------------------------------------------
# Background: _run_background sends notification on success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_background_notifies_on_success(tool: SpawnTeamTool, store: TeamsStore, notifier: AsyncMock) -> None:
    team = store.get_team("researcher")
    assert team is not None
    subset: dict = {}

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(return_value=("research done", 200))
        MockRunner.return_value = instance

        await tool._run_background("abc12345", "researcher", team, subset, "find AI news")

    notifier.send.assert_awaited_once()
    msg = notifier.send.call_args[0][0]
    assert "abc12345" in msg
    assert "research done" in msg


@pytest.mark.asyncio
async def test_background_logs_task_on_success(tool: SpawnTeamTool, store: TeamsStore) -> None:
    team = store.get_team("researcher")
    assert team is not None

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(return_value=("done", 150))
        MockRunner.return_value = instance
        await tool._run_background("j1", "researcher", team, {}, "task")

    stats = store.team_stats("researcher")
    assert stats["tasks_total"] == 1
    assert stats["tasks_success"] == 1
    assert store.monthly_tokens_used("researcher") == 150


@pytest.mark.asyncio
async def test_background_notifies_on_failure(tool: SpawnTeamTool, store: TeamsStore, notifier: AsyncMock) -> None:
    team = store.get_team("researcher")
    assert team is not None

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        instance = MagicMock()
        instance.run = AsyncMock(side_effect=RuntimeError("agent crashed"))
        MockRunner.return_value = instance
        await tool._run_background("err1", "researcher", team, {}, "crash task")

    stats = store.team_stats("researcher")
    assert stats["tasks_success"] == 0
    msg = notifier.send.call_args[0][0]
    assert "err1" in msg
    assert "ERROR" in msg or "❌" in msg


# ---------------------------------------------------------------------------
# Tool subset safety
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_team_excluded_from_tool_subset(store: TeamsStore, config: MagicMock) -> None:
    spawn_team_mock = MagicMock()
    spawn_team_mock.name = "spawn_team"
    spawn_agent_mock = MagicMock()
    spawn_agent_mock.name = "spawn_agent"
    web_mock = MagicMock()
    web_mock.name = "web_search"
    web_mock.description = "Search"
    web_mock.input_schema = {"type": "object", "properties": {}}

    registry = {"web_search": web_mock, "spawn_team": spawn_team_mock, "spawn_agent": spawn_agent_mock}
    store.create_team("safe_team", "Safe", "role", ["web_search", "spawn_team", "spawn_agent"])

    notifier = AsyncMock()
    t = SpawnTeamTool(store=store, config=config, tool_registry=registry, notifier=notifier)

    captured_tools: dict = {}

    def capture_runner(**kwargs: object) -> MagicMock:
        captured_tools.update(kwargs.get("tools", {}))  # type: ignore[arg-type]
        inst = MagicMock()
        inst.run = AsyncMock(return_value=("done", 0))
        return inst

    with patch("src.tools.spawn_team.SubAgentRunner", side_effect=capture_runner):
        team = store.get_team("safe_team")
        assert team is not None
        await t._run_background("x", "safe_team", team, {
            k: v for k, v in registry.items()
            if k in {"web_search"}
        }, "task")

    assert "spawn_team" not in captured_tools
    assert "spawn_agent" not in captured_tools


# ---------------------------------------------------------------------------
# Cancellation via JobRegistry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_kills_background_task(
    store: TeamsStore, config: MagicMock, notifier: AsyncMock
) -> None:
    """Task stored in JobRegistry can be cancelled; CancelledError is handled cleanly."""
    from src.jobs import JobRegistry

    job_registry = JobRegistry()
    tool = SpawnTeamTool(store=store, config=config, tool_registry={}, notifier=notifier, job_registry=job_registry)

    async def _hang(*a: object, **kw: object) -> tuple[str, int]:
        await asyncio.sleep(9999)
        return ("", 0)

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        runner_instance = AsyncMock()
        runner_instance.run = _hang
        MockRunner.return_value = runner_instance

        result = await tool.execute(team_id="researcher", task="do research")
        assert "delegated" in result.lower()

        # Give event loop a tick so the background task starts
        await asyncio.sleep(0)

        # Find the job_id from registry
        running = job_registry.list_running()
        assert len(running) == 1
        job_id = running[0]["job_id"]

        killed = job_registry.cancel(job_id)
        assert killed

        # Let cancellation propagate
        await asyncio.sleep(0.05)

        job = job_registry.get(job_id)
        assert job is not None
        assert job["status"] == "failed"
        assert job["error"] == "Cancelled"


@pytest.mark.asyncio
async def test_job_registry_tracks_team_job(
    store: TeamsStore, config: MagicMock, notifier: AsyncMock
) -> None:
    """spawn_team registers job in JobRegistry and marks it done on completion."""
    from src.jobs import JobRegistry

    job_registry = JobRegistry()
    tool = SpawnTeamTool(store=store, config=config, tool_registry={}, notifier=notifier, job_registry=job_registry)

    with patch("src.tools.spawn_team.SubAgentRunner") as MockRunner:
        runner_instance = AsyncMock()
        runner_instance.run = AsyncMock(return_value=("Research done.", 100))
        MockRunner.return_value = runner_instance

        await tool.execute(team_id="researcher", task="do research")
        await asyncio.sleep(0.1)

    all_jobs = job_registry.list_all()
    assert len(all_jobs) == 1
    assert all_jobs[0]["status"] == "done"
    assert all_jobs[0]["type"] == "team"
