"""Scope check guardrail — validates URLs and file paths."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

# Only these API hosts are reachable
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "api.anthropic.com",
        "api.search.brave.com",
        "api.telegram.org",
        "api.github.com",
        "github.com",
        "www.github.com",
        "www.googleapis.com",
        "oauth2.googleapis.com",
    }
)

_TRAVERSAL_RE = re.compile(r"\.\.[/\\]")

# Params that contain free-text prompts/descriptions — not URLs or paths.
# URL scheme validation is skipped for these; traversal checks still apply.
_FREETEXT_PARAMS: frozenset[str] = frozenset(
    {
        "task",
        "prompt",
        "reason",
        "fact",
        "query",
        "message",
        "content",
        "context",
        "description",
        "changes_summary",
    }
)


class ScopeCheckHook:
    name = "scope_check"

    async def check(self, tool_name: str, params: dict[str, Any]) -> tuple[bool, str | None]:
        for key, value in params.items():
            if not isinstance(value, str):
                continue
            # Skip URL scheme checks for free-text params (they naturally contain URLs)
            if key not in _FREETEXT_PARAMS:
                if value.startswith(("http://", "https://")):
                    host = urlparse(value).netloc
                    if host not in ALLOWED_HOSTS:
                        return False, f"URL host '{host}' not in allowlist (param: {key})"
                elif "://" in value or value.startswith("//"):
                    # Non-http/https scheme (ftp://, file://) or protocol-relative URL
                    return False, f"URL scheme not allowed in param '{key}'"
            # URL-decode before traversal check to catch ..%2F and similar
            # (applies to ALL params including free-text)
            if _TRAVERSAL_RE.search(unquote(value)):
                return False, f"Path traversal detected in param '{key}'"
        return True, None
