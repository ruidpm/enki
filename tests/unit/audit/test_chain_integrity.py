"""Tests for Tier1 chain hash integrity under concurrency."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.audit.db import AuditDB
from src.audit.events import Tier1Event
from src.audit.query import AuditQuery


@pytest.fixture
def db(tmp_path: Path) -> AuditDB:
    return AuditDB(tmp_path / "audit.db")


@pytest.mark.asyncio
async def test_concurrent_tier1_inserts_no_chain_fork(db: AuditDB) -> None:
    """Concurrent Tier1 inserts must produce a valid sequential chain, not forks."""
    tasks = [
        db.log_tier1(Tier1Event.GUARDRAIL_BLOCK, f"sess-{i}", {"tool": f"t{i}"})
        for i in range(20)
    ]
    await asyncio.gather(*tasks)

    q = AuditQuery(db)
    valid, msg = q.verify_chain()
    assert valid is True, f"Chain forked under concurrency: {msg}"


@pytest.mark.asyncio
async def test_chain_hashes_are_sequential(db: AuditDB) -> None:
    """Each record's prev_chain_hash must equal the previous record's chain_hash."""
    for i in range(5):
        await db.log_tier1(Tier1Event.SESSION_START, f"s{i}", {"n": i})

    with db._conn() as conn:
        rows = conn.execute(
            "SELECT chain_hash, prev_chain_hash FROM tier1 ORDER BY id"
        ).fetchall()

    assert rows[0]["prev_chain_hash"] == ""
    for i in range(1, len(rows)):
        assert rows[i]["prev_chain_hash"] == rows[i - 1]["chain_hash"], (
            f"Record {i}: prev_chain_hash doesn't match previous chain_hash"
        )
