"""Tests for ManagePipelineTool pause/resume actions."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline.store import PipelineStatus, PipelineStore
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
# pause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_active_pipeline(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="t")
    result = await tool.execute(action="pause", pipeline_id="p1")
    assert "paused" in result.lower()

    p = pipeline_store.get("p1")
    assert p is not None
    assert p["status"] == PipelineStatus.PAUSED


@pytest.mark.asyncio
async def test_pause_completed_pipeline_returns_error(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="t")
    pipeline_store.set_status("p1", PipelineStatus.COMPLETED)

    result = await tool.execute(action="pause", pipeline_id="p1")
    assert "error" in result.lower() or "cannot" in result.lower()

    p = pipeline_store.get("p1")
    assert p is not None
    assert p["status"] == PipelineStatus.COMPLETED


@pytest.mark.asyncio
async def test_pause_aborted_pipeline_returns_error(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="t")
    pipeline_store.set_status("p1", PipelineStatus.ABORTED)

    result = await tool.execute(action="pause", pipeline_id="p1")
    assert "error" in result.lower() or "cannot" in result.lower()


@pytest.mark.asyncio
async def test_pause_unknown_pipeline_returns_error(tool: ManagePipelineTool) -> None:
    result = await tool.execute(action="pause", pipeline_id="ghost")
    assert "not found" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_paused_pipeline(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="t")
    pipeline_store.set_status("p1", PipelineStatus.PAUSED)

    result = await tool.execute(action="resume", pipeline_id="p1")
    assert "active" in result.lower() or "resumed" in result.lower()

    p = pipeline_store.get("p1")
    assert p is not None
    assert p["status"] == PipelineStatus.ACTIVE


@pytest.mark.asyncio
async def test_resume_active_pipeline_returns_error(tool: ManagePipelineTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("p1", workspace_id="ws1", task="t")

    result = await tool.execute(action="resume", pipeline_id="p1")
    assert "error" in result.lower() or "already" in result.lower()

    p = pipeline_store.get("p1")
    assert p is not None
    assert p["status"] == PipelineStatus.ACTIVE


@pytest.mark.asyncio
async def test_resume_unknown_pipeline_returns_error(tool: ManagePipelineTool) -> None:
    result = await tool.execute(action="resume", pipeline_id="ghost")
    assert "not found" in result.lower() or "error" in result.lower()
