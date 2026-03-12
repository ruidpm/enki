"""Tests for C-06: SQLite store locks — verify asyncio.Lock protects DB operations."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.pipeline.store import PipelineStore
from src.teams.store import TeamsStore
from src.workspaces.store import WorkspaceStore


@pytest.fixture
def teams_store(tmp_path: Path) -> TeamsStore:
    return TeamsStore(tmp_path / "teams.db")


@pytest.fixture
def pipeline_store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(tmp_path / "pipelines.db")


@pytest.fixture
def workspace_store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path / "workspaces.db")


# ---------------------------------------------------------------------------
# Verify stores have an asyncio.Lock
# ---------------------------------------------------------------------------


def test_teams_store_has_lock(teams_store: TeamsStore) -> None:
    assert hasattr(teams_store, "_lock")
    assert isinstance(teams_store._lock, asyncio.Lock)


def test_pipeline_store_has_lock(pipeline_store: PipelineStore) -> None:
    assert hasattr(pipeline_store, "_lock")
    assert isinstance(pipeline_store._lock, asyncio.Lock)


def test_workspace_store_has_lock(workspace_store: WorkspaceStore) -> None:
    assert hasattr(workspace_store, "_lock")
    assert isinstance(workspace_store._lock, asyncio.Lock)


# ---------------------------------------------------------------------------
# Verify concurrent writes don't corrupt data (lock protects operations)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_team_creates_are_serialized(teams_store: TeamsStore) -> None:
    """Multiple concurrent create_team calls should all succeed without DB errors."""

    async def _create(i: int) -> None:
        await teams_store.create_team_async(
            team_id=f"team-{i}",
            name=f"Team {i}",
            role=f"Role {i}",
            tools=["web_search"],
        )

    await asyncio.gather(*[_create(i) for i in range(20)])
    teams = await teams_store.list_teams_async()
    assert len(teams) == 20


@pytest.mark.asyncio
async def test_concurrent_pipeline_creates_are_serialized(pipeline_store: PipelineStore) -> None:
    """Multiple concurrent pipeline creates should all succeed."""

    async def _create(i: int) -> None:
        await pipeline_store.create_async(f"p-{i}", workspace_id="ws1", task=f"task {i}")

    await asyncio.gather(*[_create(i) for i in range(20)])
    all_p = await pipeline_store.list_all_async()
    assert len(all_p) == 20


@pytest.mark.asyncio
async def test_concurrent_workspace_adds_are_serialized(workspace_store: WorkspaceStore) -> None:
    """Multiple concurrent workspace adds should all succeed."""

    async def _add(i: int) -> None:
        await workspace_store.add_async(f"ws-{i}", name=f"WS {i}", local_path=f"/path/{i}")

    await asyncio.gather(*[_add(i) for i in range(20)])
    all_ws = await workspace_store.list_all_async()
    assert len(all_ws) == 20
