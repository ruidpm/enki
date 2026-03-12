"""Tests for Tier2 purge scheduling — M-07."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.audit.db import AuditDB
from src.audit.events import Tier2Event


@pytest.fixture
def db(tmp_path: Path) -> AuditDB:
    return AuditDB(tmp_path / "audit.db")


@pytest.mark.asyncio
async def test_purge_old_tier2_deletes_old_records(db: AuditDB) -> None:
    """purge_old_tier2 must delete records older than N days."""
    import json
    from datetime import UTC, datetime, timedelta

    # Insert a record with a timestamp 60 days ago
    old_ts = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO tier2 (event_type, session_id, timestamp, data) VALUES (?, ?, ?, ?)",
            ("TOOL_CALLED", "old-session", old_ts, json.dumps({"tool": "old"})),
        )

    # Insert a recent record
    await db.log_tier2(Tier2Event.TOOL_CALLED, "new-session", {"tool": "new"})

    deleted = db.purge_old_tier2(30)
    assert deleted == 1

    # New record should still exist
    with db._conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM tier2").fetchone()[0]
    assert count == 1


def test_purge_called_at_startup() -> None:
    """Verify purge_old_tier2 is called during agent startup in main.py."""
    # We verify the startup hook in main.py calls audit.purge_old_tier2
    from pathlib import Path

    main_path = Path(__file__).resolve().parents[3] / "main.py"
    source = main_path.read_text()
    assert "purge_old_tier2" in source, "main.py must call audit.purge_old_tier2() at startup"
