"""Tests for AuditHook — record() called after guardrail chain decision."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.guardrails.audit_hook import AuditHook


@pytest.fixture
def mock_writer() -> AsyncMock:
    writer = AsyncMock()
    writer.log_tool_call = AsyncMock()
    return writer


@pytest.fixture
def hook(mock_writer: AsyncMock) -> AuditHook:
    return AuditHook(writer=mock_writer, session_id="test-session")


@pytest.mark.asyncio
async def test_record_called_for_allowed_tool(hook: AuditHook, mock_writer: AsyncMock) -> None:
    """record() must log allowed tool calls with name and params."""
    params = {"query": "test search"}
    await hook.record("web_search", params, allowed=True, reason=None)

    mock_writer.log_tool_call.assert_awaited_once_with(
        tool_name="web_search",
        params={"query": "test search"},
        allowed=True,
        block_reason=None,
        session_id="test-session",
    )


@pytest.mark.asyncio
async def test_record_called_for_blocked_tool(hook: AuditHook, mock_writer: AsyncMock) -> None:
    """record() must log blocked tool calls with name, params, and reason."""
    params = {"action": "delete"}
    await hook.record("evil_tool", params, allowed=False, reason="[allowlist] not registered")

    mock_writer.log_tool_call.assert_awaited_once_with(
        tool_name="evil_tool",
        params={"action": "delete"},
        allowed=False,
        block_reason="[allowlist] not registered",
        session_id="test-session",
    )


@pytest.mark.asyncio
async def test_check_always_allows(hook: AuditHook) -> None:
    """check() must always return (True, None) — audit hook never blocks."""
    allow, reason = await hook.check("any_tool", {"key": "val"})
    assert allow is True
    assert reason is None
