"""Tests for PipelineStatusTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline.store import PipelineStore
from src.tools.pipeline_status import PipelineStatusTool


@pytest.fixture
def pipeline_store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(tmp_path / "pipelines.db")


@pytest.fixture
def tool(pipeline_store: PipelineStore) -> PipelineStatusTool:
    return PipelineStatusTool(pipeline_store=pipeline_store)


# ---------------------------------------------------------------------------
# No pipeline_id → list active pipelines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_pipelines(tool: PipelineStatusTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("pipe-001", workspace_id="ws1", task="Add login feature")
    pipeline_store.create("pipe-002", workspace_id="ws2", task="Fix auth bug")

    result = await tool.execute()

    assert "pipe-001" in result
    assert "pipe-002" in result
    assert "Add login feature" in result
    assert "Fix auth bug" in result
    assert "RESEARCH" in result  # default stage uppercased


@pytest.mark.asyncio
async def test_list_active_shows_stage_and_status(tool: PipelineStatusTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("pipe-003", workspace_id="ws1", task="Build API")
    pipeline_store.advance_stage("pipe-003", "implement")

    result = await tool.execute()

    assert "pipe-003" in result
    assert "IMPLEMENT" in result
    assert "active" in result


# ---------------------------------------------------------------------------
# No active pipelines → friendly message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_active_pipelines(tool: PipelineStatusTool) -> None:
    result = await tool.execute()
    assert "no active pipelines" in result.lower()


@pytest.mark.asyncio
async def test_no_active_excludes_completed(tool: PipelineStatusTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("pipe-done", workspace_id="ws1", task="Old task")
    pipeline_store.set_status("pipe-done", "completed")

    result = await tool.execute()
    assert "no active pipelines" in result.lower()


# ---------------------------------------------------------------------------
# With pipeline_id → detailed status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_shows_pipeline_info(tool: PipelineStatusTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("pipe-100", workspace_id="ws1", task="Refactor auth module")
    pipeline_store.advance_stage("pipe-100", "plan")

    result = await tool.execute(pipeline_id="pipe-100")

    assert "pipe-100" in result
    assert "Refactor auth module" in result
    assert "active" in result
    assert "PLAN" in result


@pytest.mark.asyncio
async def test_detail_shows_artifacts(tool: PipelineStatusTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("pipe-200", workspace_id="ws1", task="Add caching")
    pipeline_store.save_artifact("pipe-200", "research", "notes", "Research notes here")
    pipeline_store.save_artifact("pipe-200", "scope", "scope_doc", "Scope document")

    result = await tool.execute(pipeline_id="pipe-200")

    assert "Artifacts:" in result
    assert "RESEARCH" in result
    assert "notes" in result
    assert "SCOPE" in result
    assert "scope_doc" in result


@pytest.mark.asyncio
async def test_detail_no_artifacts(tool: PipelineStatusTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("pipe-300", workspace_id="ws1", task="Empty pipeline")

    result = await tool.execute(pipeline_id="pipe-300")

    assert "(none yet)" in result


@pytest.mark.asyncio
async def test_detail_shows_gate_fields_when_present(tool: PipelineStatusTool, pipeline_store: PipelineStore) -> None:
    """Gate columns may not exist yet; tool should display them when present."""
    pipeline_store.create("pipe-400", workspace_id="ws1", task="Gated pipeline")
    pipeline_store.save_artifact("pipe-400", "review", "report", "Review content")

    # Manually add gate columns if the migration adds them
    try:
        pipeline_store._conn.execute("ALTER TABLE pipeline_artifacts ADD COLUMN gate_verdict TEXT")
        pipeline_store._conn.execute("ALTER TABLE pipeline_artifacts ADD COLUMN gate_score REAL")
        pipeline_store._conn.execute("ALTER TABLE pipeline_artifacts ADD COLUMN gist_url TEXT")
        pipeline_store._conn.commit()
    except Exception:
        pass  # columns may already exist

    pipeline_store._conn.execute(
        """
        UPDATE pipeline_artifacts
        SET gate_verdict = 'pass', gate_score = 0.95, gist_url = 'https://gist.github.com/abc'
        WHERE pipeline_id = 'pipe-400' AND stage = 'review'
        """,
    )
    pipeline_store._conn.commit()

    result = await tool.execute(pipeline_id="pipe-400")

    assert "gate=pass" in result
    assert "score=0.9" in result  # 0.95 formatted to 1 decimal
    assert "https://gist.github.com/abc" in result


# ---------------------------------------------------------------------------
# Pipeline not found → error message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_not_found(tool: PipelineStatusTool) -> None:
    result = await tool.execute(pipeline_id="ghost-pipe")
    assert "not found" in result.lower()
    assert "ghost-pipe" in result


# ---------------------------------------------------------------------------
# Artifacts listed in stage order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifacts_in_stage_order(tool: PipelineStatusTool, pipeline_store: PipelineStore) -> None:
    pipeline_store.create("pipe-500", workspace_id="ws1", task="Ordered test")
    # Insert in reverse order
    pipeline_store.save_artifact("pipe-500", "plan", "plan_doc", "Plan content")
    pipeline_store.save_artifact("pipe-500", "research", "notes", "Research content")

    result = await tool.execute(pipeline_id="pipe-500")

    # research should appear before plan (insertion id order matches this
    # since save_artifact uses AUTOINCREMENT — but we inserted plan first)
    # Actually the store orders by id (insertion order), and we inserted
    # plan first, so plan would come before research by id.
    # The tool should ideally sort by stage order, but the store returns by id.
    # Let's just verify both appear.
    assert "RESEARCH" in result
    assert "PLAN" in result

    # Check that research appears before plan in the output
    # The store returns by insertion order (id), so plan comes first.
    # We need the tool to sort by stage order.
    research_pos = result.index("RESEARCH")
    plan_pos = result.index("PLAN")
    # If tool sorts by stage order, research < plan
    # The current store.list_artifacts orders by id, so we need the tool to re-sort
    assert research_pos < plan_pos, "Artifacts should be ordered by stage progression"
