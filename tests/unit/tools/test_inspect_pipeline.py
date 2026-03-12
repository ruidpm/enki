"""Tests for InspectPipelineTool — pipeline metrics inspection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.pipeline.store import PipelineStore
from src.tools.inspect_pipeline import InspectPipelineTool


@pytest.fixture
def store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(tmp_path / "pipelines.db")


@pytest.fixture
def tool(store: PipelineStore) -> InspectPipelineTool:
    return InspectPipelineTool(pipeline_store=store)


@pytest.fixture
def seeded_store(store: PipelineStore) -> PipelineStore:
    """Store with a pipeline and some step data."""
    store.create("p1", workspace_id="ws1", task="build app")
    store.save_step(
        "p1",
        "research",
        1,
        input_tokens=500,
        output_tokens=200,
        cost_usd=0.005,
        tools_called_json=json.dumps([{"name": "web_search", "input": "q", "output": "r"}]),
        duration_ms=1200,
    )
    store.save_step(
        "p1",
        "research",
        2,
        input_tokens=800,
        output_tokens=300,
        cost_usd=0.008,
        tools_called_json=json.dumps([{"name": "notes", "input": "n", "output": "ok"}]),
        duration_ms=900,
    )
    store.save_step(
        "p1",
        "scope",
        1,
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.02,
        tools_called_json="[]",
        duration_ms=2000,
    )
    return store


@pytest.mark.asyncio
async def test_summary_returns_table(seeded_store: PipelineStore) -> None:
    tool = InspectPipelineTool(pipeline_store=seeded_store)
    result = await tool.execute(pipeline_id="p1", action="summary")
    assert "Pipeline p1" in result
    assert "research" in result
    assert "scope" in result
    assert "Total" in result
    assert "$" in result


@pytest.mark.asyncio
async def test_steps_returns_all(seeded_store: PipelineStore) -> None:
    tool = InspectPipelineTool(pipeline_store=seeded_store)
    result = await tool.execute(pipeline_id="p1", action="steps")
    assert "Step 1 [research]" in result
    assert "Step 2 [research]" in result
    assert "Step 1 [scope]" in result
    assert "web_search" in result


@pytest.mark.asyncio
async def test_steps_filtered_by_stage(seeded_store: PipelineStore) -> None:
    tool = InspectPipelineTool(pipeline_store=seeded_store)
    result = await tool.execute(pipeline_id="p1", action="steps", stage="research")
    assert "Step 1 [research]" in result
    assert "Step 2 [research]" in result
    assert "scope" not in result.lower().split("stage:")[0] or "scope" not in result


@pytest.mark.asyncio
async def test_missing_pipeline_returns_error(tool: InspectPipelineTool) -> None:
    result = await tool.execute(pipeline_id="ghost", action="summary")
    assert "ERROR" in result
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_no_steps_returns_message(store: PipelineStore) -> None:
    store.create("p2", workspace_id="ws1", task="empty pipeline")
    tool = InspectPipelineTool(pipeline_store=store)
    result = await tool.execute(pipeline_id="p2", action="steps")
    assert "No steps" in result


@pytest.mark.asyncio
async def test_missing_pipeline_id_returns_error(tool: InspectPipelineTool) -> None:
    result = await tool.execute(action="summary")
    assert "ERROR" in result
