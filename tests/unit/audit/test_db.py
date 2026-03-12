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


# ---------------------------------------------------------------------------
# C-01: Sanitize tool params in Tier 2 audit logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sanitize_params_redacts_sensitive_keys(db: AuditDB) -> None:
    """Sensitive keys in tool params must be redacted before Tier 2 logging."""
    params = {
        "query": "weather today",
        "api_key": "sk-secret-123",
        "password": "hunter2",
        "token": "ghp_abc",
        "secret": "shhh",
        "authorization": "Bearer xyz",
        "imap_password": "mail-pass",
        "anthropic_api_key": "sk-ant-xyz",
        "credential": "cred-value",
    }
    await db.log_tool_call("web_search", params, allowed=True, block_reason=None, session_id="s1")
    q = AuditQuery(db)
    summary = q.get_session_summary("s1")
    stored = summary["events"][0]["data"]
    stored_params = stored["params"]
    # Non-sensitive key preserved
    assert stored_params["query"] == "weather today"
    # All sensitive keys redacted
    for key in ("api_key", "password", "token", "secret", "authorization", "imap_password", "anthropic_api_key", "credential"):
        assert stored_params[key] == "[REDACTED]", f"{key} was not redacted"


@pytest.mark.asyncio
async def test_sanitize_params_handles_nested_sensitive_keys(db: AuditDB) -> None:
    """Nested dicts with sensitive keys should also be redacted."""
    params = {"config": {"api_key": "secret", "host": "localhost"}, "name": "test"}
    await db.log_tool_call("some_tool", params, allowed=True, block_reason=None, session_id="s2")
    q = AuditQuery(db)
    summary = q.get_session_summary("s2")
    stored = summary["events"][0]["data"]
    assert stored["params"]["config"]["api_key"] == "[REDACTED]"
    assert stored["params"]["config"]["host"] == "localhost"
    assert stored["params"]["name"] == "test"


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
