"""Tests for system health check tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.health_check import HealthCheckTool


class TestHealthCheckTool:
    """HealthCheckTool should report system health status."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "data"
        d.mkdir()
        return d

    @pytest.fixture
    def tool(self, data_dir: Path) -> HealthCheckTool:
        return HealthCheckTool(data_dir=data_dir)

    def test_tool_metadata(self, tool: HealthCheckTool) -> None:
        assert tool.name == "health_check"
        assert "health" in tool.description.lower()
        assert tool.input_schema["type"] == "object"

    @pytest.mark.asyncio
    async def test_reports_missing_databases(self, tool: HealthCheckTool) -> None:
        """If no DBs exist yet, health check should report them as missing."""
        result = await tool.execute()
        assert "missing" in result.lower() or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_reports_existing_databases(self, data_dir: Path) -> None:
        """If DBs exist, health check should report their sizes."""
        # Create a minimal SQLite DB
        import sqlite3

        db_path = data_dir / "audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        tool = HealthCheckTool(data_dir=data_dir)
        result = await tool.execute()
        assert "audit.db" in result

    @pytest.mark.asyncio
    async def test_reports_disk_usage(self, data_dir: Path) -> None:
        """Health check should include data directory size."""
        # Create some files
        (data_dir / "test.db").write_bytes(b"x" * 1024)
        tool = HealthCheckTool(data_dir=data_dir)
        result = await tool.execute()
        # Should mention total size
        assert "total" in result.lower() or "disk" in result.lower() or "size" in result.lower()

    @pytest.mark.asyncio
    async def test_reports_integrity_ok(self, data_dir: Path) -> None:
        """Valid SQLite DB should pass integrity check."""
        import sqlite3

        db_path = data_dir / "memory.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE turns (id INTEGER)")
        conn.commit()
        conn.close()

        tool = HealthCheckTool(data_dir=data_dir)
        result = await tool.execute()
        assert "ok" in result.lower() or "pass" in result.lower()

    @pytest.mark.asyncio
    async def test_reports_corrupted_db(self, data_dir: Path) -> None:
        """Corrupted SQLite file should be flagged."""
        bad_db = data_dir / "tasks.db"
        bad_db.write_bytes(b"not a sqlite database at all")

        tool = HealthCheckTool(data_dir=data_dir)
        result = await tool.execute()
        assert "fail" in result.lower() or "error" in result.lower() or "corrupt" in result.lower()

    @pytest.mark.asyncio
    async def test_api_connectivity_check(self, data_dir: Path) -> None:
        """Should include API connectivity status (will fail in test env without real key)."""
        tool = HealthCheckTool(data_dir=data_dir, api_key="test-invalid-key")
        result = await tool.execute()
        # Should mention API status even if it fails
        assert "api" in result.lower()

    @pytest.mark.asyncio
    async def test_scheduler_info_when_provided(self, data_dir: Path) -> None:
        """If scheduler is wired, should report job count."""
        tool = HealthCheckTool(data_dir=data_dir)
        tool.set_scheduler_info(job_count=4, running=True)
        result = await tool.execute()
        assert "scheduler" in result.lower()
        assert "4" in result

    @pytest.mark.asyncio
    async def test_no_params_required(self, tool: HealthCheckTool) -> None:
        """Tool should work with zero parameters."""
        result = await tool.execute()
        assert isinstance(result, str)
        assert len(result) > 0
