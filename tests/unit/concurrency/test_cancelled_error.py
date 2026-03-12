"""Tests for C-09: CancelledError must be re-raised after cleanup in pipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import ModelId
from src.pipeline.store import PipelineStatus, PipelineStore
from src.teams.store import TeamsStore
from src.tools.run_pipeline import RunPipelineTool
from src.workspaces.store import WorkspaceStore


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.anthropic_api_key = "test-key"
    cfg.haiku_model = ModelId.HAIKU
    return cfg


@pytest.fixture
def pipeline_store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(tmp_path / "pipelines.db")


@pytest.fixture
def workspace_store(tmp_path: Path) -> WorkspaceStore:
    store = WorkspaceStore(tmp_path / "ws.db")
    ws_path = tmp_path / "myapp"
    ws_path.mkdir()
    store.add("ws1", name="MyApp", local_path=str(ws_path), language="python")
    return store


@pytest.fixture
def teams_store(tmp_path: Path) -> TeamsStore:
    store = TeamsStore(tmp_path / "teams.db")
    store.create_team("researcher", "Researcher", "role", ["web_search"])
    return store


@pytest.mark.asyncio
async def test_cancelled_error_is_reraised_after_cleanup(
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """CancelledError in _run_background must be re-raised after cleanup.

    Python's async cancellation contract requires CancelledError to propagate.
    The old code used `return` instead of `raise`, violating this contract.
    """
    notifier = AsyncMock()
    notifier.ask_single_confirm = AsyncMock(return_value=True)
    notifier.send = AsyncMock()
    notifier.ask_free_text = AsyncMock(return_value=None)

    tool = RunPipelineTool(
        notifier=notifier,
        pipeline_store=pipeline_store,
        workspace_store=workspace_store,
        teams_store=teams_store,
        config=_make_config(),
        tool_registry={},
    )

    pipeline_id = "cancel_reraise"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build it")

    async def _hang(*a: object, **kw: object) -> tuple[str, int]:
        await asyncio.sleep(9999)
        return ("", 0)

    with patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner:
        runner_instance = AsyncMock()
        runner_instance.run = _hang
        MockRunner.return_value = runner_instance

        bg = asyncio.create_task(
            tool._run_background(
                pipeline_id=pipeline_id,
                task="build it",
                workspace=workspace_store.get("ws1"),
            )
        )

        await asyncio.sleep(0)
        bg.cancel()

        # CancelledError MUST propagate — the task must be marked cancelled
        with pytest.raises(asyncio.CancelledError):
            await bg

    # Cleanup should still have happened
    p = pipeline_store.get(pipeline_id)
    assert p is not None
    assert p["status"] == PipelineStatus.ABORTED
