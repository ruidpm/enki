"""Tests for JobRegistry and JobStatusTool."""

from __future__ import annotations

import pytest

from src.jobs import JobRegistry, JobStatus
from src.tools.job_status import JobStatusTool


@pytest.fixture
def registry() -> JobRegistry:
    return JobRegistry()


@pytest.fixture
def tool(registry: JobRegistry) -> JobStatusTool:
    return JobStatusTool(registry=registry)


# ---------------------------------------------------------------------------
# JobRegistry
# ---------------------------------------------------------------------------


def test_register_and_get(registry: JobRegistry) -> None:
    registry.start("job1", job_type="ccc", description="build auth")
    job = registry.get("job1")
    assert job is not None
    assert job["job_id"] == "job1"
    assert job["type"] == "ccc"
    assert job["description"] == "build auth"
    assert job["status"] == JobStatus.RUNNING
    assert job["stage"] is None


def test_update_stage(registry: JobRegistry) -> None:
    registry.start("job1", job_type="pipeline", description="add login")
    registry.update_stage("job1", "IMPLEMENT")
    job = registry.get("job1")
    assert job is not None
    assert job["stage"] == "IMPLEMENT"


def test_finish_success(registry: JobRegistry) -> None:
    registry.start("job1", job_type="ccc", description="task")
    registry.finish("job1", success=True)
    job = registry.get("job1")
    assert job is not None
    assert job["status"] == JobStatus.DONE
    assert job["ended_at"] is not None


def test_finish_failure(registry: JobRegistry) -> None:
    registry.start("job1", job_type="pipeline", description="task")
    registry.finish("job1", success=False, error="API down")
    job = registry.get("job1")
    assert job is not None
    assert job["status"] == JobStatus.FAILED
    assert job["error"] == "API down"


def test_list_running_excludes_finished(registry: JobRegistry) -> None:
    registry.start("job1", job_type="ccc", description="a")
    registry.start("job2", job_type="pipeline", description="b")
    registry.finish("job1", success=True)

    running = registry.list_running()
    ids = {j["job_id"] for j in running}
    assert "job1" not in ids
    assert "job2" in ids


def test_list_all_includes_finished(registry: JobRegistry) -> None:
    registry.start("job1", job_type="ccc", description="a")
    registry.finish("job1", success=True)

    all_jobs = registry.list_all()
    assert any(j["job_id"] == "job1" for j in all_jobs)


def test_list_running_empty(registry: JobRegistry) -> None:
    assert registry.list_running() == []


def test_elapsed_seconds(registry: JobRegistry) -> None:
    registry.start("job1", job_type="ccc", description="task")
    job = registry.get("job1")
    assert job is not None
    assert job["elapsed_s"] >= 0.0


def test_unknown_job_returns_none(registry: JobRegistry) -> None:
    assert registry.get("ghost") is None


def test_update_stage_unknown_job_is_noop(registry: JobRegistry) -> None:
    registry.update_stage("ghost", "PLAN")  # should not raise


def test_finish_unknown_job_is_noop(registry: JobRegistry) -> None:
    registry.finish("ghost", success=True)  # should not raise


# ---------------------------------------------------------------------------
# JobStatusTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_no_jobs(tool: JobStatusTool) -> None:
    result = await tool.execute()
    assert "no" in result.lower() or "idle" in result.lower() or result.strip() != ""


@pytest.mark.asyncio
async def test_status_shows_running_jobs(tool: JobStatusTool, registry: JobRegistry) -> None:
    registry.start("abc12345", job_type="pipeline", description="add login feature")
    registry.update_stage("abc12345", "PLAN")

    result = await tool.execute()
    assert "abc12345" in result
    assert "pipeline" in result.lower() or "add login" in result.lower()
    assert "plan" in result.lower()


@pytest.mark.asyncio
async def test_status_all_flag_includes_finished(tool: JobStatusTool, registry: JobRegistry) -> None:
    registry.start("done01", job_type="ccc", description="build thing")
    registry.finish("done01", success=True)

    result = await tool.execute(show_all=True)
    assert "done01" in result


@pytest.mark.asyncio
async def test_status_specific_job(tool: JobStatusTool, registry: JobRegistry) -> None:
    registry.start("xyz99", job_type="spawn_team", description="research AI trends")
    registry.update_stage("xyz99", "running")

    result = await tool.execute(job_id="xyz99")
    assert "xyz99" in result
    assert "research" in result.lower()


@pytest.mark.asyncio
async def test_status_unknown_job(tool: JobStatusTool) -> None:
    result = await tool.execute(job_id="ghost")
    assert "not found" in result.lower() or "error" in result.lower()
