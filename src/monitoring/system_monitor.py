"""System health monitor — background checks that alert on problems."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger()

_EXPECTED_DBS = [
    "audit.db",
    "memory.db",
    "tasks.db",
    "teams.db",
    "workspaces.db",
    "pipelines.db",
    "schedule.db",
]

# Default: warn at 2 GB
_DEFAULT_DISK_WARN_BYTES = 2 * 1024 * 1024 * 1024


def _human_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


@dataclass
class SystemAlert:
    severity: str  # "warning" or "error"
    message: str


class SystemMonitor:
    def __init__(
        self,
        data_dir: Path,
        disk_warn_bytes: int = _DEFAULT_DISK_WARN_BYTES,
    ) -> None:
        self._data_dir = data_dir
        self._disk_warn_bytes = disk_warn_bytes

    def run_checks(self) -> list[SystemAlert]:
        """Run all health checks and return any alerts."""
        alerts: list[SystemAlert] = []
        alerts.extend(self.check_databases())
        alerts.extend(self.check_disk_usage())
        return alerts

    def check_databases(self) -> list[SystemAlert]:
        """Check existence and integrity of expected SQLite databases."""
        alerts: list[SystemAlert] = []
        for db_name in _EXPECTED_DBS:
            db_path = self._data_dir / db_name
            if not db_path.exists():
                alerts.append(
                    SystemAlert(
                        severity="warning",
                        message=f"{db_name}: missing, not created yet",
                    )
                )
                continue

            try:
                conn = sqlite3.connect(str(db_path))
                result = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                if result and str(result[0]) != "ok":
                    alerts.append(
                        SystemAlert(
                            severity="error",
                            message=f"{db_name}: integrity check failed — {result[0]}",
                        )
                    )
            except Exception as exc:
                alerts.append(
                    SystemAlert(
                        severity="error",
                        message=f"{db_name}: integrity check error — {exc}",
                    )
                )

        return alerts

    def check_disk_usage(self) -> list[SystemAlert]:
        """Alert if data directory exceeds size threshold."""
        if not self._data_dir.exists():
            return []

        total = sum(f.stat().st_size for f in self._data_dir.rglob("*") if f.is_file())
        if total >= self._disk_warn_bytes:
            return [
                SystemAlert(
                    severity="warning",
                    message=f"data directory: {_human_size(total)}, above {_human_size(self._disk_warn_bytes)} threshold",
                )
            ]
        return []

    @staticmethod
    def format_alerts(alerts: list[SystemAlert]) -> str:
        """Format alerts into a message for the agent. Empty string if no alerts."""
        if not alerts:
            return ""

        lines = ["SYSTEM HEALTH ALERT"]
        for alert in alerts:
            prefix = "ERROR" if alert.severity == "error" else "WARNING"
            lines.append(f"  [{prefix}] {alert.message}")
        return "\n".join(lines)
