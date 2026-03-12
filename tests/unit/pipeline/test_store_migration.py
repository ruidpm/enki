"""Tests for PipelineStore migration — new columns on pipeline_artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline.store import PipelineStage, PipelineStore


@pytest.fixture
def store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(tmp_path / "pipelines.db")


# ---------------------------------------------------------------------------
# Column existence
# ---------------------------------------------------------------------------


def test_new_columns_exist_after_init(store: PipelineStore) -> None:
    """gist_url, gate_verdict, gate_score columns should exist on fresh init."""
    store.create("p1", workspace_id="ws1", task="t")
    store.save_artifact("p1", PipelineStage.RESEARCH, "report", "content")
    art = store.get_artifact("p1", PipelineStage.RESEARCH)
    assert art is not None
    # New columns default to None
    assert art["gist_url"] is None
    assert art["gate_verdict"] is None
    assert art["gate_score"] is None


# ---------------------------------------------------------------------------
# update_artifact_gate
# ---------------------------------------------------------------------------


def test_update_artifact_gate_stores_values(store: PipelineStore) -> None:
    """update_artifact_gate should set gate_verdict, gate_score, gist_url."""
    store.create("p1", workspace_id="ws1", task="t")
    store.save_artifact("p1", PipelineStage.RESEARCH, "report", "content")

    store.update_artifact_gate(
        "p1",
        PipelineStage.RESEARCH,
        gate_verdict="pass",
        gate_score=0.95,
        gist_url="https://gist.github.com/abc",
    )

    art = store.get_artifact("p1", PipelineStage.RESEARCH)
    assert art is not None
    assert art["gate_verdict"] == "pass"
    assert art["gate_score"] == pytest.approx(0.95)
    assert art["gist_url"] == "https://gist.github.com/abc"


def test_update_artifact_gate_partial(store: PipelineStore) -> None:
    """gate_score and gist_url are optional — only verdict is required."""
    store.create("p1", workspace_id="ws1", task="t")
    store.save_artifact("p1", PipelineStage.RESEARCH, "report", "content")

    store.update_artifact_gate(
        "p1",
        PipelineStage.RESEARCH,
        gate_verdict="retry",
    )

    art = store.get_artifact("p1", PipelineStage.RESEARCH)
    assert art is not None
    assert art["gate_verdict"] == "retry"
    assert art["gate_score"] is None
    assert art["gist_url"] is None


@pytest.mark.asyncio
async def test_update_artifact_gate_async(store: PipelineStore) -> None:
    """Async wrapper should work identically."""
    store.create("p1", workspace_id="ws1", task="t")
    store.save_artifact("p1", PipelineStage.RESEARCH, "report", "content")

    await store.update_artifact_gate_async(
        "p1",
        PipelineStage.RESEARCH,
        gate_verdict="escalate",
        gate_score=0.3,
    )

    art = store.get_artifact("p1", PipelineStage.RESEARCH)
    assert art is not None
    assert art["gate_verdict"] == "escalate"
    assert art["gate_score"] == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Idempotent migration
# ---------------------------------------------------------------------------


def test_migration_idempotent(tmp_path: Path) -> None:
    """Creating PipelineStore twice on same DB should not error."""
    db_path = tmp_path / "pipelines.db"
    store1 = PipelineStore(db_path)
    store1.create("p1", workspace_id="ws1", task="t")
    store1.save_artifact("p1", PipelineStage.RESEARCH, "report", "content")
    store1.update_artifact_gate("p1", PipelineStage.RESEARCH, gate_verdict="pass", gate_score=0.9)

    # Second init should not fail — migration is idempotent
    store2 = PipelineStore(db_path)
    art = store2.get_artifact("p1", PipelineStage.RESEARCH)
    assert art is not None
    assert art["gate_verdict"] == "pass"
