"""Tests for AuditDB."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.audit.db import AuditDB
from src.audit.events import Tier1Event, Tier2Event
from src.audit.query import AuditQuery
from src.models import ModelId


@pytest.fixture
def db(tmp_path: Path) -> AuditDB:
    return AuditDB(tmp_path / "audit.db")


@pytest.mark.asyncio
async def test_log_tier1_stores_event(db: AuditDB) -> None:
    await db.log_tier1(Tier1Event.SESSION_START, "sess1", {"interface": "cli"})
    q = AuditQuery(db)
    events = q.get_security_events(session_id="sess1")
    assert len(events) == 1
    assert events[0]["event_type"] == "SESSION_START"


@pytest.mark.asyncio
async def test_tier1_chain_is_valid(db: AuditDB) -> None:
    for i in range(5):
        await db.log_tier1(Tier1Event.GUARDRAIL_BLOCK, f"s{i}", {"tool": "evil"})
    q = AuditQuery(db)
    valid, msg = q.verify_chain()
    assert valid is True, msg


@pytest.mark.asyncio
async def test_log_tier2_stores_event(db: AuditDB) -> None:
    await db.log_tier2(Tier2Event.TOOL_CALLED, "sess1", {"tool": "web_search"})
    q = AuditQuery(db)
    summary = q.get_session_summary("sess1")
    assert summary["event_count"] == 1
    assert summary["events"][0]["event_type"] == "TOOL_CALLED"


@pytest.mark.asyncio
async def test_log_tool_call_blocked_goes_to_tier1(db: AuditDB) -> None:
    await db.log_tool_call("evil_tool", {}, allowed=False, block_reason="not registered", session_id="s1")
    q = AuditQuery(db)
    events = q.get_security_events(session_id="s1")
    assert len(events) == 1
    assert events[0]["event_type"] == Tier1Event.GUARDRAIL_BLOCK


@pytest.mark.asyncio
async def test_log_tool_call_allowed_goes_to_tier2(db: AuditDB) -> None:
    await db.log_tool_call("web_search", {}, allowed=True, block_reason=None, session_id="s1")
    q = AuditQuery(db)
    events = q.get_security_events(session_id="s1")
    assert len(events) == 0  # nothing in Tier 1
    summary = q.get_session_summary("s1")
    assert summary["event_count"] == 1


@pytest.mark.asyncio
async def test_cost_query(db: AuditDB) -> None:
    await db.log_tier2(
        Tier2Event.LLM_CALL, "s1", {"model": ModelId.SONNET, "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.001}
    )
    q = AuditQuery(db)
    costs = q.get_costs()
    assert costs["total_input_tokens"] == 100
    assert costs["total_output_tokens"] == 50
    assert costs["total_cost_usd"] == pytest.approx(0.001)
