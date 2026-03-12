"""Tests for ScopeCheckHook — including adversarial bypass attempts."""

from __future__ import annotations

import pytest

from src.guardrails.scope_check import ScopeCheckHook


@pytest.fixture
def hook() -> ScopeCheckHook:
    return ScopeCheckHook()


@pytest.mark.asyncio
async def test_allows_no_urls(hook: ScopeCheckHook) -> None:
    allow, _ = await hook.check("tasks", {"action": "list"})
    assert allow is True


@pytest.mark.asyncio
async def test_allows_approved_host(hook: ScopeCheckHook) -> None:
    allow, _ = await hook.check("web_search", {"url": "https://api.search.brave.com/res"})
    assert allow is True


@pytest.mark.asyncio
async def test_blocks_unapproved_host(hook: ScopeCheckHook) -> None:
    allow, reason = await hook.check("web_search", {"url": "https://evil.com/steal"})
    assert allow is False
    assert "not in allowlist" in (reason or "")


@pytest.mark.asyncio
async def test_blocks_path_traversal(hook: ScopeCheckHook) -> None:
    allow, reason = await hook.check("notes", {"project": "../../etc/passwd"})
    assert allow is False
    assert "traversal" in (reason or "").lower()


@pytest.mark.asyncio
async def test_blocks_windows_traversal(hook: ScopeCheckHook) -> None:
    allow, reason = await hook.check("notes", {"project": "..\\windows\\system32"})
    assert allow is False


@pytest.mark.asyncio
async def test_ignores_non_string_params(hook: ScopeCheckHook) -> None:
    allow, _ = await hook.check("tasks", {"count": 5, "active": True})
    assert allow is True
