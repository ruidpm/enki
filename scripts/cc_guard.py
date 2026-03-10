#!/usr/bin/env python3
"""Claude Code PreToolUse guard — blocks writes to protected paths.

Receives JSON on stdin (Claude Code hook protocol):
  {"tool_name": "Write", "tool_input": {"file_path": "...", ...}}

Exits 1 (with explanation to stderr) to block, exits 0 to allow.

PROTECTED_PATHS is defined here independently of the main codebase
so this guard works even if imports are broken.
"""
from __future__ import annotations

import json
import sys

# Mirror of PROTECTED_PATHS in src/tools/claude_code.py — keep in sync.
PROTECTED_PATHS: frozenset[str] = frozenset({
    "src/guardrails/",
    "src/audit/",
    "src/agent.py",
    "main.py",
    "src/config.py",
    "scripts/cc_guard.py",  # guard cannot disable itself
    ".claude/settings.json",  # hook config cannot be removed
})


def _extract_path(tool_name: str, tool_input: dict[str, object]) -> str:
    """Pull the target file path from tool params."""
    if tool_name in ("Write", "Edit", "MultiEdit"):
        return str(tool_input.get("file_path", ""))
    return ""


def _is_protected(path: str) -> bool:
    # Normalise: strip leading ./ prefix or leading /
    normalized = path.removeprefix("./").lstrip("/")
    for protected in PROTECTED_PATHS:
        if normalized == protected or normalized.startswith(protected):
            return True
    return False


def main() -> int:
    try:
        data: dict[str, object] = json.load(sys.stdin)
    except Exception:
        return 0  # can't parse → don't block

    tool_name = str(data.get("tool_name", ""))
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0

    path = _extract_path(tool_name, tool_input)
    if not path:
        return 0

    if _is_protected(path):
        print(
            f"[cc_guard] BLOCKED: '{path}' is a protected path.\n"
            f"Protected paths cannot be modified by Claude Code.\n"
            f"Paths: {', '.join(sorted(PROTECTED_PATHS))}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
