"""Tests for SavePipelineArtifactTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline.store import PipelineStore
from src.tools.save_pipeline_artifact import SavePipelineArtifactTool


@pytest.fixture
def store(tmp_path: Path) -> PipelineStore:
    s = PipelineStore(tmp_path / "pipelines.db")
    s.create("p1", workspace_id="ws1", task="build auth")
    return s


@pytest.fixture
def tool(store: PipelineStore) -> SavePipelineArtifactTool:
    return SavePipelineArtifactTool(pipeline_store=store)


@pytest.mark.asyncio
async def test_save_artifact_succeeds(tool: SavePipelineArtifactTool, store: PipelineStore) -> None:
    result = await tool.execute(
        pipeline_id="p1",
        stage="research",
        artifact_type="research_report",
        content="Found these findings: ...",
    )
    assert "saved" in result.lower() or "ok" in result.lower() or "p1" in result

    artifact = store.get_artifact("p1", "research")
    assert artifact is not None
    assert artifact["content"] == "Found these findings: ..."
    assert artifact["artifact_type"] == "research_report"


@pytest.mark.asyncio
async def test_save_artifact_unknown_pipeline(tool: SavePipelineArtifactTool) -> None:
    result = await tool.execute(
        pipeline_id="ghost",
        stage="research",
        artifact_type="research_report",
        content="stuff",
    )
    assert "error" in result.lower() or "not found" in result.lower()


@pytest.mark.asyncio
async def test_save_artifact_invalid_stage(tool: SavePipelineArtifactTool) -> None:
    result = await tool.execute(
        pipeline_id="p1",
        stage="explode",
        artifact_type="whatever",
        content="stuff",
    )
    assert "error" in result.lower() or "invalid" in result.lower()


@pytest.mark.asyncio
async def test_save_artifact_overwrites_existing(tool: SavePipelineArtifactTool, store: PipelineStore) -> None:
    await tool.execute(
        pipeline_id="p1",
        stage="research",
        artifact_type="research_report",
        content="first version",
    )
    await tool.execute(
        pipeline_id="p1",
        stage="research",
        artifact_type="research_report",
        content="updated version",
    )
    artifact = store.get_artifact("p1", "research")
    assert artifact is not None
    assert artifact["content"] == "updated version"


@pytest.mark.asyncio
async def test_save_artifact_missing_required_fields(tool: SavePipelineArtifactTool) -> None:
    result = await tool.execute(pipeline_id="p1")
    assert "error" in result.lower() or "required" in result.lower()


@pytest.mark.asyncio
async def test_save_multiple_stages(tool: SavePipelineArtifactTool, store: PipelineStore) -> None:
    await tool.execute(pipeline_id="p1", stage="research", artifact_type="research_report", content="findings")
    await tool.execute(pipeline_id="p1", stage="scope", artifact_type="requirements", content="requirements doc")
    artifacts = store.list_artifacts("p1")
    assert len(artifacts) == 2
