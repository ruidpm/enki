"""Tests for system health monitor — background checks that alert Enki on problems."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.monitoring.system_monitor import SystemAlert, SystemMonitor


class TestDatabaseChecks:
    """Monitor should detect DB problems."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "data"
        d.mkdir()
        return d

    @pytest.fixture
    def monitor(self, data_dir: Path) -> SystemMonitor:
        return SystemMonitor(data_dir=data_dir)

    def test_missing_db_generates_alert(self, monitor: SystemMonitor) -> None:
        alerts = monitor.check_databases()
        # All expected DBs are missing
        assert any(a.severity == "warning" for a in alerts)
        assert any("missing" in a.message.lower() for a in alerts)

    def test_healthy_db_no_alert(self, data_dir: Path) -> None:
        # Create a valid DB
        db_path = data_dir / "audit.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.commit()
        conn.close()

        monitor = SystemMonitor(data_dir=data_dir)
        alerts = monitor.check_databases()
        # audit.db should NOT generate an alert
        audit_alerts = [a for a in alerts if "audit.db" in a.message]
        assert not any("integrity" in a.message.lower() and "fail" in a.message.lower() for a in audit_alerts)

    def test_corrupted_db_generates_alert(self, data_dir: Path) -> None:
        bad_db = data_dir / "memory.db"
        bad_db.write_bytes(b"this is not sqlite")

        monitor = SystemMonitor(data_dir=data_dir)
        alerts = monitor.check_databases()
        assert any("memory.db" in a.message and a.severity == "error" for a in alerts)


class TestDiskChecks:
    """Monitor should alert on high disk usage."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "data"
        d.mkdir()
        return d

    def test_disk_usage_reported(self, data_dir: Path) -> None:
        (data_dir / "big.db").write_bytes(b"x" * 10_000)
        monitor = SystemMonitor(data_dir=data_dir)
        alerts = monitor.check_disk_usage()
        # Under threshold — no alerts
        assert not alerts

    def test_disk_usage_alert_over_threshold(self, data_dir: Path) -> None:
        monitor = SystemMonitor(data_dir=data_dir, disk_warn_bytes=100)
        (data_dir / "big.db").write_bytes(b"x" * 200)
        alerts = monitor.check_disk_usage()
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"


class TestRunAllChecks:
    """run_checks() should aggregate all check results."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "data"
        d.mkdir()
        return d

    def test_run_checks_returns_alerts(self, data_dir: Path) -> None:
        monitor = SystemMonitor(data_dir=data_dir)
        alerts = monitor.run_checks()
        assert isinstance(alerts, list)
        assert all(isinstance(a, SystemAlert) for a in alerts)


class TestAlertFormatting:
    """Alerts should format into a message Enki can relay."""

    def test_format_alerts_for_agent(self) -> None:
        alerts = [
            SystemAlert(severity="error", message="memory.db: integrity check failed"),
            SystemAlert(severity="warning", message="data directory: 4.2 GB, above 2 GB threshold"),
        ]
        msg = SystemMonitor.format_alerts(alerts)
        assert "SYSTEM HEALTH" in msg
        assert "memory.db" in msg
        assert "4.2 GB" in msg

    def test_format_empty_alerts(self) -> None:
        msg = SystemMonitor.format_alerts([])
        assert msg == ""
