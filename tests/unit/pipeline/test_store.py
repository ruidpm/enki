"""Tests for PipelineStore."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline.store import PipelineStage, PipelineStatus, PipelineStore


@pytest.fixture
def store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(tmp_path / "pipelines.db")


# ---------------------------------------------------------------------------
# Pipeline lifecycle
# ---------------------------------------------------------------------------

def test_create_and_get(store: PipelineStore) -> None:
    store.create("p1", workspace_id="ws1", task="add auth")
    p = store.get("p1")
    assert p is not None
    assert p["workspace_id"] == "ws1"
    assert p["task"] == "add auth"
    assert p["status"] == PipelineStatus.ACTIVE
    assert p["current_stage"] == PipelineStage.RESEARCH


def test_get_unknown_returns_none(store: PipelineStore) -> None:
    assert store.get("nope") is None


def test_list_active(store: PipelineStore) -> None:
    store.create("p1", workspace_id="ws1", task="t1")
    store.create("p2", workspace_id="ws2", task="t2")
    store.set_status("p2", PipelineStatus.COMPLETED)
    active = store.list_active()
    assert len(active) == 1
    assert active[0]["pipeline_id"] == "p1"


def test_list_all(store: PipelineStore) -> None:
    store.create("p1", workspace_id="ws1", task="t1")
    store.create("p2", workspace_id="ws2", task="t2")
    store.set_status("p2", PipelineStatus.ABORTED)
    assert len(store.list_all()) == 2


# ---------------------------------------------------------------------------
# Stage transitions
# ---------------------------------------------------------------------------

def test_advance_stage(store: PipelineStore) -> None:
    store.create("p1", workspace_id="ws1", task="t")
    store.advance_stage("p1", PipelineStage.SCOPE)
    p = store.get("p1")
    assert p is not None
    assert p["current_stage"] == PipelineStage.SCOPE


def test_advance_through_all_stages(store: PipelineStore) -> None:
    store.create("p1", workspace_id="ws1", task="t")
    stages = [
        PipelineStage.SCOPE,
        PipelineStage.PLAN,
        PipelineStage.IMPLEMENT,
        PipelineStage.TEST,
        PipelineStage.REVIEW,
        PipelineStage.PR,
    ]
    for stage in stages:
        store.advance_stage("p1", stage)
    p = store.get("p1")
    assert p is not None
    assert p["current_stage"] == PipelineStage.PR


def test_set_status(store: PipelineStore) -> None:
    store.create("p1", workspace_id="ws1", task="t")
    store.set_status("p1", PipelineStatus.ABORTED)
    p = store.get("p1")
    assert p is not None
    assert p["status"] == PipelineStatus.ABORTED


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def test_save_and_get_artifact(store: PipelineStore) -> None:
    store.create("p1", workspace_id="ws1", task="t")
    store.save_artifact("p1", PipelineStage.RESEARCH, "research_report", "Found X and Y.")
    artifact = store.get_artifact("p1", PipelineStage.RESEARCH)
    assert artifact is not None
    assert artifact["content"] == "Found X and Y."
    assert artifact["artifact_type"] == "research_report"


def test_get_artifact_missing_returns_none(store: PipelineStore) -> None:
    store.create("p1", workspace_id="ws1", task="t")
    assert store.get_artifact("p1", PipelineStage.SCOPE) is None


def test_save_artifact_overwrites_existing(store: PipelineStore) -> None:
    store.create("p1", workspace_id="ws1", task="t")
    store.save_artifact("p1", PipelineStage.RESEARCH, "research_report", "First version.")
    store.save_artifact("p1", PipelineStage.RESEARCH, "research_report", "Second version.")
    artifact = store.get_artifact("p1", PipelineStage.RESEARCH)
    assert artifact is not None
    assert artifact["content"] == "Second version."


def test_list_artifacts(store: PipelineStore) -> None:
    store.create("p1", workspace_id="ws1", task="t")
    store.save_artifact("p1", PipelineStage.RESEARCH, "research_report", "R")
    store.save_artifact("p1", PipelineStage.SCOPE, "requirements", "S")
    arts = store.list_artifacts("p1")
    assert len(arts) == 2
