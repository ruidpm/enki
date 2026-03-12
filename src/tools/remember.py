"""Remember/forget tools — immediate fact persistence to facts.md."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


class RememberTool:
    name = "remember"
    description = (
        "Store a fact about the user immediately in persistent memory. "
        "Use when the user explicitly asks to remember something, or when you "
        "learn a durable preference, commitment, or life detail worth keeping."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "The fact to remember (e.g. 'User prefers dark mode')",
            },
        },
        "required": ["fact"],
    }

    def __init__(self, facts_path: Path) -> None:
        self._facts_path = facts_path

    async def execute(self, **kwargs: Any) -> str:
        fact = kwargs.get("fact", "").strip()
        if not fact:
            return "Cannot remember an empty fact."

        # Strip leading bullet if user/agent included one
        if fact.startswith("- "):
            fact = fact[2:]

        self._facts_path.parent.mkdir(parents=True, exist_ok=True)

        # Read existing facts and check for duplicates
        existing = ""
        if self._facts_path.exists():
            existing = self._facts_path.read_text()
            # Check for exact duplicate (case-insensitive)
            existing_lower = existing.lower()
            if fact.lower() in existing_lower:
                return f"Already remembered: {fact}"

        # Append the new fact
        with self._facts_path.open("a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(f"- {fact}\n")

        log.info("fact_remembered", fact=fact[:80])
        return f"Remembered: {fact}"


class ForgetTool:
    name = "forget"
    description = (
        "Remove a fact from persistent memory. Use when the user explicitly "
        "asks to forget something or when a stored fact is no longer true."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "The fact to forget — partial match is fine (e.g. 'dark mode')",
            },
        },
        "required": ["fact"],
    }

    def __init__(self, facts_path: Path) -> None:
        self._facts_path = facts_path

    async def execute(self, **kwargs: Any) -> str:
        query = kwargs.get("fact", "").strip()
        if not query:
            return "Cannot forget an empty query."

        if not self._facts_path.exists():
            return "No facts stored yet."

        lines = self._facts_path.read_text().splitlines()
        query_lower = query.lower()

        kept: list[str] = []
        removed: list[str] = []
        for line in lines:
            if query_lower in line.lower() and line.strip():
                removed.append(line)
            else:
                kept.append(line)

        if not removed:
            return f"No matching fact found for: {query}"

        self._facts_path.write_text("\n".join(kept) + "\n" if kept else "")
        log.info("fact_forgotten", query=query, removed_count=len(removed))
        return f"Forgot {len(removed)} fact(s) matching '{query}'."
