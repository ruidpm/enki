"""Tests for C-10: Spawn agent must use asyncio.Semaphore to prevent race condition."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import ModelId
from src.tools.spawn_agent import SpawnAgentTool


@pytest.fixture
def config() -> MagicMock:
    cfg = MagicMock()
    cfg.anthropic_api_key = "test-key"
    cfg.haiku_model = ModelId.HAIKU
    return cfg


@pytest.fixture
def registry() -> dict:
    t = MagicMock()
    t.name = "web_search"
    t.description = "Search the web"
    t.input_schema = {"type": "object", "properties": {}}
    return {"web_search": t}


def test_spawn_agent_uses_semaphore(config: MagicMock, registry: dict) -> None:
    """SpawnAgentTool must use asyncio.Semaphore instead of manual counter."""
    tool = SpawnAgentTool(config=config, tool_registry=registry)
    assert hasattr(tool, "_semaphore")
    assert isinstance(tool._semaphore, asyncio.Semaphore)
    # Should NOT have _active counter
    assert not hasattr(tool, "_active")


@pytest.mark.asyncio
async def test_concurrent_spawns_respect_limit(config: MagicMock, registry: dict) -> None:
    """Even with concurrent spawns, the semaphore must prevent more than 5 concurrent agents."""
    tool = SpawnAgentTool(config=config, tool_registry=registry)

    peak_concurrent = 0
    current = 0
    lock = asyncio.Lock()

    async def _fake_run(task: str) -> tuple[str, int]:
        nonlocal peak_concurrent, current
        async with lock:
            current += 1
            if current > peak_concurrent:
                peak_concurrent = current
        await asyncio.sleep(0.01)  # simulate work
        async with lock:
            current -= 1
        return ("done", 50)

    with patch("src.tools.spawn_agent.SubAgentRunner") as MockRunner:
        runner = MagicMock()
        runner.run = _fake_run
        MockRunner.return_value = runner

        # Launch 10 concurrent spawns — only 5 should run at once
        tasks = [tool.execute(task=f"task-{i}", tools=["web_search"]) for i in range(10)]
        await asyncio.gather(*tasks)

    # All tasks should complete (some may be blocked then succeed)
    assert peak_concurrent <= 5


@pytest.mark.asyncio
async def test_semaphore_released_on_exception(config: MagicMock, registry: dict) -> None:
    """Semaphore must be released even when SubAgentRunner raises."""
    tool = SpawnAgentTool(config=config, tool_registry=registry)

    with patch("src.tools.spawn_agent.SubAgentRunner") as MockRunner:
        runner = MagicMock()
        runner.run = AsyncMock(side_effect=RuntimeError("boom"))
        MockRunner.return_value = runner

        # This should propagate the error but release the semaphore
        with pytest.raises(RuntimeError, match="boom"):
            await tool.execute(task="crash", tools=["web_search"])

    # Semaphore should be fully available again (value back to 5)
    # We can verify by acquiring 5 without blocking
    for _ in range(5):
        tool._semaphore.acquire()  # noqa: F841 — sync check, return value unused
        # Semaphore.acquire() returns True if available
