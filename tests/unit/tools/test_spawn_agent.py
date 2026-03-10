"""Tests for SpawnAgentTool and SubAgentRunner."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.spawn_agent import SpawnAgentTool
from src.sub_agent import SubAgentRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config() -> MagicMock:
    cfg = MagicMock()
    cfg.anthropic_api_key = "test-key"
    cfg.default_model = "claude-haiku-4-5-20251001"
    return cfg


@pytest.fixture
def mock_tool() -> MagicMock:
    t = MagicMock()
    t.name = "web_search"
    t.description = "Search the web"
    t.input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    t.execute = AsyncMock(return_value="Search results: nothing found")
    return t


@pytest.fixture
def registry(mock_tool: MagicMock) -> dict:
    return {"web_search": mock_tool}


@pytest.fixture
def spawn_tool(config: MagicMock, registry: dict) -> SpawnAgentTool:
    return SpawnAgentTool(config=config, tool_registry=registry)


# ---------------------------------------------------------------------------
# SpawnAgentTool — basic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_runs_task_and_returns_result(
    spawn_tool: SpawnAgentTool, config: MagicMock
) -> None:
    with patch("src.tools.spawn_agent.SubAgentRunner") as MockRunner:
        runner = MagicMock()
        runner.run = AsyncMock(return_value=("Research complete: ...", 100))
        MockRunner.return_value = runner

        result = await spawn_tool.execute(task="research something", tools=["web_search"])

    assert "Research complete" in result
    runner.run.assert_awaited_once_with("research something")


@pytest.mark.asyncio
async def test_spawn_never_passes_spawn_agent_to_subagent(
    spawn_tool: SpawnAgentTool, registry: dict
) -> None:
    """Sub-agents must not be able to recursively spawn agents."""
    # Add spawn_agent to registry to verify it gets filtered
    registry["spawn_agent"] = MagicMock(name="spawn_agent")

    with patch("src.tools.spawn_agent.SubAgentRunner") as MockRunner:
        runner = MagicMock()
        runner.run = AsyncMock(return_value=("done", 50))
        MockRunner.return_value = runner

        await spawn_tool.execute(task="task", tools=["web_search", "spawn_agent"])

    # Check the subset passed to SubAgentRunner excludes spawn_agent
    call_kwargs = MockRunner.call_args[1]
    subset = call_kwargs.get("tools") or MockRunner.call_args[0][1]
    assert "spawn_agent" not in subset


@pytest.mark.asyncio
async def test_spawn_uses_requested_model(spawn_tool: SpawnAgentTool) -> None:
    with patch("src.tools.spawn_agent.SubAgentRunner") as MockRunner:
        runner = MagicMock()
        runner.run = AsyncMock(return_value=("ok", 50))
        MockRunner.return_value = runner

        await spawn_tool.execute(task="task", model="claude-opus-4-6", tools=["web_search"])

    call_kwargs = MockRunner.call_args[1]
    model = call_kwargs.get("model") or MockRunner.call_args[0][2]
    assert model == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_spawn_unknown_tool_produces_empty_subset(spawn_tool: SpawnAgentTool) -> None:
    """Requesting tools not in registry should silently filter them out."""
    with patch("src.tools.spawn_agent.SubAgentRunner") as MockRunner:
        runner = MagicMock()
        runner.run = AsyncMock(return_value=("ok", 50))
        MockRunner.return_value = runner

        result = await spawn_tool.execute(task="task", tools=["nonexistent_tool"])

    assert result == "ok"


# ---------------------------------------------------------------------------
# Concurrency limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_limit_blocks_excess_spawns(
    config: MagicMock, registry: dict
) -> None:
    tool = SpawnAgentTool(config=config, tool_registry=registry)
    tool._active = 5  # simulate max active

    result = await tool.execute(task="task", tools=["web_search"])
    assert "BLOCKED" in result or "limit" in result.lower()


# ---------------------------------------------------------------------------
# SubAgentRunner — end-turn (no tool use)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runner_returns_text_on_end_turn(config: MagicMock, mock_tool: MagicMock) -> None:
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Here is the answer."
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [text_block]
    response.usage.input_tokens = 10
    response.usage.output_tokens = 5

    with patch("src.sub_agent.anthropic.AsyncAnthropic") as mock_client_cls:
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.create = AsyncMock(return_value=response)
        mock_client_cls.return_value = client

        runner = SubAgentRunner(config=config, tools={"web_search": mock_tool},
                                model="claude-haiku-4-5-20251001", max_tokens=1024)
        text, tokens = await runner.run("What is 2+2?")

    assert "Here is the answer." in text
    assert tokens == 15


@pytest.mark.asyncio
async def test_runner_calls_tool_and_continues(config: MagicMock, mock_tool: MagicMock) -> None:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "web_search"
    tool_block.id = "tu_001"
    tool_block.input = {"query": "latest news"}

    tool_use_response = MagicMock()
    tool_use_response.stop_reason = "tool_use"
    tool_use_response.content = [tool_block]
    tool_use_response.usage.input_tokens = 20
    tool_use_response.usage.output_tokens = 10

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Based on search: nothing new."

    end_response = MagicMock()
    end_response.stop_reason = "end_turn"
    end_response.content = [text_block]
    end_response.usage.input_tokens = 30
    end_response.usage.output_tokens = 15

    with patch("src.sub_agent.anthropic.AsyncAnthropic") as mock_client_cls:
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.create = AsyncMock(side_effect=[tool_use_response, end_response])
        mock_client_cls.return_value = client

        runner = SubAgentRunner(config=config, tools={"web_search": mock_tool},
                                model="claude-haiku-4-5-20251001", max_tokens=1024)
        text, tokens = await runner.run("Search for news")

    assert "nothing new" in text
    assert tokens == 75  # 20+10+30+15
    mock_tool.execute.assert_awaited_once_with(query="latest news")


@pytest.mark.asyncio
async def test_runner_handles_unknown_tool_gracefully(config: MagicMock) -> None:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "nonexistent"
    tool_block.id = "tu_002"
    tool_block.input = {}

    tool_use_response = MagicMock()
    tool_use_response.stop_reason = "tool_use"
    tool_use_response.content = [tool_block]
    tool_use_response.usage.input_tokens = 10
    tool_use_response.usage.output_tokens = 5

    end_response = MagicMock()
    end_response.stop_reason = "end_turn"
    end_response.content = []
    end_response.usage.input_tokens = 20
    end_response.usage.output_tokens = 8

    with patch("src.sub_agent.anthropic.AsyncAnthropic") as mock_client_cls:
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.create = AsyncMock(side_effect=[tool_use_response, end_response])
        mock_client_cls.return_value = client

        runner = SubAgentRunner(config=config, tools={},
                                model="claude-haiku-4-5-20251001", max_tokens=1024)
        text, tokens = await runner.run("use a tool")

    assert isinstance(text, str)
    assert tokens == 43  # 10+5+20+8
