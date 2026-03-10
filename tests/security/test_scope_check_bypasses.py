"""Security tests — ScopeCheckHook must resist all URL/path bypass techniques."""
from __future__ import annotations

import pytest

from src.guardrails.scope_check import ScopeCheckHook


@pytest.fixture
def hook() -> ScopeCheckHook:
    return ScopeCheckHook()


# ---------------------------------------------------------------------------
# URL allowlist bypasses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blocks_ftp_scheme(hook: ScopeCheckHook) -> None:
    """ftp:// URLs must be blocked — non-http/https schemes not allowed."""
    allow, reason = await hook.check("web_search", {"url": "ftp://evil.com/steal"})
    assert allow is False
    assert reason is not None


@pytest.mark.asyncio
async def test_blocks_protocol_relative_url(hook: ScopeCheckHook) -> None:
    """//evil.com/path is protocol-relative — must be blocked."""
    allow, reason = await hook.check("web_search", {"url": "//evil.com/steal"})
    assert allow is False
    assert reason is not None


@pytest.mark.asyncio
async def test_blocks_subdomain_of_allowed(hook: ScopeCheckHook) -> None:
    """evil.api.anthropic.com is NOT api.anthropic.com — must be blocked."""
    allow, reason = await hook.check("web_search", {"url": "https://evil.api.anthropic.com/steal"})
    assert allow is False
    assert "not in allowlist" in (reason or "")


@pytest.mark.asyncio
async def test_blocks_allowed_host_as_path(hook: ScopeCheckHook) -> None:
    """https://evil.com/api.anthropic.com/steal — host is evil.com, not allowed."""
    allow, reason = await hook.check("web_search", {"url": "https://evil.com/api.anthropic.com/steal"})
    assert allow is False


@pytest.mark.asyncio
async def test_blocks_userinfo_smuggling(hook: ScopeCheckHook) -> None:
    """https://api.anthropic.com@evil.com/ — real host is evil.com via userinfo."""
    allow, reason = await hook.check("web_search", {"url": "https://api.anthropic.com@evil.com/"})
    # urlparse netloc = "api.anthropic.com@evil.com" — not in allowlist
    assert allow is False


@pytest.mark.asyncio
async def test_blocks_reverse_userinfo_smuggling(hook: ScopeCheckHook) -> None:
    """https://evil.com@api.anthropic.com/ — netloc includes evil.com@."""
    allow, reason = await hook.check("web_search", {"url": "https://evil.com@api.anthropic.com/"})
    # netloc = "evil.com@api.anthropic.com" — not in allowlist
    assert allow is False


@pytest.mark.asyncio
async def test_blocks_ipv4_loopback(hook: ScopeCheckHook) -> None:
    """SSRF via 127.0.0.1 — not in allowlist."""
    allow, reason = await hook.check("web_search", {"url": "https://127.0.0.1/internal"})
    assert allow is False


@pytest.mark.asyncio
async def test_blocks_ipv6_loopback(hook: ScopeCheckHook) -> None:
    """SSRF via IPv6 loopback ::1."""
    allow, reason = await hook.check("web_search", {"url": "https://[::1]/internal"})
    assert allow is False


@pytest.mark.asyncio
async def test_blocks_ipv6_address(hook: ScopeCheckHook) -> None:
    """Arbitrary IPv6 — not in allowlist."""
    allow, reason = await hook.check("web_search", {"url": "https://[2001:db8::1]/steal"})
    assert allow is False


@pytest.mark.asyncio
async def test_blocks_allowed_host_with_port(hook: ScopeCheckHook) -> None:
    """api.anthropic.com:8080 — netloc includes port, not in allowlist as-is."""
    allow, reason = await hook.check("web_search", {"url": "https://api.anthropic.com:8080/v1"})
    # Fail-secure: port makes the netloc mismatch — blocked.
    assert allow is False


@pytest.mark.asyncio
async def test_blocks_url_in_non_url_param(hook: ScopeCheckHook) -> None:
    """A URL appearing in an arbitrary param key is still validated."""
    allow, reason = await hook.check("tasks", {"callback": "https://evil.com/exfil"})
    assert allow is False


@pytest.mark.asyncio
async def test_allows_allowed_host_no_port(hook: ScopeCheckHook) -> None:
    """Clean allowlisted URL passes."""
    allow, _ = await hook.check("web_search", {"url": "https://api.search.brave.com/res/v1/web/search?q=x"})
    assert allow is True


# ---------------------------------------------------------------------------
# Path traversal bypasses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blocks_encoded_traversal(hook: ScopeCheckHook) -> None:
    """..%2F..%2F is URL-encoded path traversal — must be blocked after URL-decoding."""
    allow, reason = await hook.check("notes", {"project": "..%2F..%2Fetc%2Fpasswd"})
    assert allow is False
    assert reason is not None


@pytest.mark.asyncio
async def test_blocks_double_slash_traversal(hook: ScopeCheckHook) -> None:
    """Null-byte and double-slash combinations."""
    allow, reason = await hook.check("notes", {"project": "../../etc/passwd"})
    assert allow is False


@pytest.mark.asyncio
async def test_blocks_mixed_traversal(hook: ScopeCheckHook) -> None:
    """../ combined with valid path prefix."""
    allow, reason = await hook.check("notes", {"project": "projects/../../secret"})
    assert allow is False


@pytest.mark.asyncio
async def test_traversal_in_nested_param(hook: ScopeCheckHook) -> None:
    """Path traversal in any string param, not just 'path' or 'project'."""
    allow, reason = await hook.check("notes", {"title": "good", "folder": "../../../etc"})
    assert allow is False


@pytest.mark.asyncio
async def test_no_false_positive_on_relative_path(hook: ScopeCheckHook) -> None:
    """Normal relative paths without traversal should pass."""
    allow, _ = await hook.check("notes", {"project": "my-project/notes.md"})
    assert allow is True


@pytest.mark.asyncio
async def test_no_false_positive_on_double_dot_in_name(hook: ScopeCheckHook) -> None:
    """File named 'report..final.md' should not trigger traversal (no slash after ..)."""
    allow, _ = await hook.check("notes", {"project": "report..final.md"})
    assert allow is True
