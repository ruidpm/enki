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


# ---------------------------------------------------------------------------
# Free-text params should NOT trigger URL scheme checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allows_url_in_task_param(hook: ScopeCheckHook) -> None:
    """The 'task' param is free-text — URLs inside should not be blocked."""
    allow, _ = await hook.check(
        "spawn_agent",
        {"task": "Analyse the repo at http://localhost:3000 and summarize"},
    )
    assert allow is True


@pytest.mark.asyncio
async def test_allows_url_in_prompt_param(hook: ScopeCheckHook) -> None:
    allow, _ = await hook.check(
        "manage_schedule",
        {"prompt": "Check https://example.com/status and report back"},
    )
    assert allow is True


@pytest.mark.asyncio
async def test_allows_scheme_in_reason_param(hook: ScopeCheckHook) -> None:
    allow, _ = await hook.check(
        "run_claude_code",
        {"task": "fix bug", "reason": "ftp://old-server is referenced in code"},
    )
    assert allow is True


@pytest.mark.asyncio
async def test_allows_url_in_fact_param(hook: ScopeCheckHook) -> None:
    allow, _ = await hook.check(
        "remember",
        {"fact": "User's blog is at https://myblog.example.com"},
    )
    assert allow is True


@pytest.mark.asyncio
async def test_still_blocks_url_in_non_freetext_param(hook: ScopeCheckHook) -> None:
    """Params like 'url', 'callback', 'path' should still be validated."""
    allow, _ = await hook.check("web_search", {"url": "https://evil.com/steal"})
    assert allow is False


@pytest.mark.asyncio
async def test_traversal_still_checked_in_freetext(hook: ScopeCheckHook) -> None:
    """Path traversal in free-text params should still be caught."""
    allow, reason = await hook.check("spawn_agent", {"task": "read ../../etc/passwd"})
    assert allow is False
    assert "traversal" in (reason or "").lower()
