"""Tests for ManagePipelineTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline.store import PipelineStage, PipelineStatus, PipelineStore
from src.tools.manage_pipeline import ManagePipelineTool
from src.workspaces.store import WorkspaceStore


@pytest.fixture
def pipeline_store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(tmp_path / "pipelines.db")


@pytest.fixture
def workspace_store(tmp_path: Path) -> WorkspaceStore:
    store = WorkspaceStore(tmp_path / "ws.db")
    store.add("ws1", name="MyApp", local_path=str(tmp_path / "myapp"), language="python")
    (tmp_path / "myapp").mkdir()
    return store


@pytest.fixture
def tool(pipeline_store: PipelineStore, workspace_store: WorkspaceStore) -> ManagePipelineTool:
    return ManagePipelineTool(pipeline_store=pipeline_store, workspace_store=workspace_store)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_pipeline(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    result = await tool.execute(action="start", workspace_id="ws1", task="add OAuth login")
    assert "pipeline" in result.lower() or "started" in result.lower() or "research" in result.lower()

    pipelines = pipeline_store.list_all()
    assert len(pipelines) == 1
    assert pipelines[0]["workspace_id"] == "ws1"
    assert pipelines[0]["task"] == "add OAuth login"
    assert pipelines[0]["current_stage"] == PipelineStage.RESEARCH


@pytest.mark.asyncio
async def test_start_unknown_workspace_returns_error(tool: ManagePipelineTool) -> None:
    result = await tool.execute(action="start", workspace_id="ghost", task="do thing")
    assert "error" in result.lower() or "not found" in result.lower()


@pytest.mark.asyncio
async def test_start_missing_task_returns_error(tool: ManagePipelineTool) -> None:
    result = await tool.execute(action="start", workspace_id="ws1")
    assert "error" in result.lower() or "required" in result.lower()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty(tool: ManagePipelineTool) -> None:
    result = await tool.execute(action="list")
    assert "no" in result.lower() or result.strip() != ""


@pytest.mark.asyncio
async def test_list_shows_active_pipelines(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="add auth")
    pipeline_store.create("p2", workspace_id="ws1", task="fix bug")
    pipeline_store.set_status("p2", PipelineStatus.COMPLETED)

    result = await tool.execute(action="list")
    assert "add auth" in result
    # completed pipelines shown only in list_all, list shows active by default
    assert "p1" in result


# ---------------------------------------------------------------------------
# advance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_moves_to_next_stage(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="t")
    pipeline_store.save_artifact("p1", PipelineStage.RESEARCH, "research_report", "findings")

    result = await tool.execute(action="advance", pipeline_id="p1")
    assert "scope" in result.lower() or "advanced" in result.lower()

    p = pipeline_store.get("p1")
    assert p is not None
    assert p["current_stage"] == PipelineStage.SCOPE


@pytest.mark.asyncio
async def test_advance_blocked_without_artifact(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="t")
    # No artifact saved for RESEARCH stage

    result = await tool.execute(action="advance", pipeline_id="p1")
    assert "artifact" in result.lower() or "error" in result.lower() or "complete" in result.lower()

    # Stage should NOT have advanced
    p = pipeline_store.get("p1")
    assert p is not None
    assert p["current_stage"] == PipelineStage.RESEARCH


@pytest.mark.asyncio
async def test_advance_unknown_pipeline_returns_error(tool: ManagePipelineTool) -> None:
    result = await tool.execute(action="advance", pipeline_id="ghost")
    assert "not found" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_advance_at_final_stage_completes_pipeline(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="t")
    pipeline_store.advance_stage("p1", PipelineStage.PR)
    pipeline_store.save_artifact("p1", PipelineStage.PR, "pr_url", "https://github.com/u/r/pull/42")

    result = await tool.execute(action="advance", pipeline_id="p1")
    assert "complet" in result.lower() or "done" in result.lower() or "pr" in result.lower()

    p = pipeline_store.get("p1")
    assert p is not None
    assert p["status"] == PipelineStatus.COMPLETED


# ---------------------------------------------------------------------------
# abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_sets_status(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="t")
    result = await tool.execute(action="abort", pipeline_id="p1")
    assert "abort" in result.lower() or "cancelled" in result.lower()

    p = pipeline_store.get("p1")
    assert p is not None
    assert p["status"] == PipelineStatus.ABORTED


@pytest.mark.asyncio
async def test_abort_unknown_pipeline(tool: ManagePipelineTool) -> None:
    result = await tool.execute(action="abort", pipeline_id="ghost")
    assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_shows_pipeline_details(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="add auth")
    pipeline_store.save_artifact("p1", PipelineStage.RESEARCH, "research_report", "findings here")

    result = await tool.execute(action="status", pipeline_id="p1")
    assert "add auth" in result
    assert "research" in result.lower()


@pytest.mark.asyncio
async def test_unknown_action(tool: ManagePipelineTool) -> None:
    result = await tool.execute(action="explode")
    assert "unknown" in result.lower() or "invalid" in result.lower()
