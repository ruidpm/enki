"""Post-session memory compactor — distills conversation into durable user facts and patterns."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import anthropic
import structlog
from anthropic.types import TextBlock

from src.models import ModelId

from .store import MemoryStore

log = structlog.get_logger()

# Step 1: Extract candidate facts from the session transcript.
# High bar — only genuinely durable, user-specific facts.
_EXTRACT_PROMPT = """\
You are a memory extractor for Enki, an AI assistant. \
Given a conversation transcript, extract ONLY genuinely durable facts about the user.

What belongs here:
- Stable preferences ("User prefers concise responses", "User likes to be addressed informally")
- Ongoing commitments or goals ("User is house-hunting in Lisbon", "User is building a personal assistant project")
- Important life context ("User works as a software engineer", "User lives in Porto")
- Recurring patterns Enki should remember ("User checks tasks every morning")
- Personal relationships and people: family members, partners, pets — always include name and role \
(e.g. "User's wife is called Ana", "User has a cat named Mochi")
- Personal attributes: city, age, nationality, health conditions relevant to daily planning

What does NOT belong:
- One-off questions or tasks ("User asked about weather today")
- Small talk
- Anything transient or session-specific
- Facts about the world rather than about the user

If nothing durable was learned this session, output nothing.
Maximum 10 facts. One fact per line, no bullets or numbering.

Transcript:
{transcript}
"""

# Step 2: Merge new facts with existing facts.md content.
# Deduplicate, remove stale entries, cap at 100.
_MERGE_PROMPT = """\
You are a memory manager for Enki, an AI assistant. \
Merge the existing user facts with newly extracted facts.

Rules:
- Keep only durable facts about the user (preferences, commitments, life context, patterns)
- Remove duplicates — if a new fact updates an old one, keep only the newer version
- Remove facts that are no longer relevant or are clearly outdated
- NEVER remove facts about named people (family members, partner, pets) — these are permanent
- Maximum 100 facts total
- Output one fact per line, no bullets or numbering, no headers

EXISTING FACTS:
{existing_facts}

NEW FACTS FROM THIS SESSION:
{new_facts}
"""

_EXTRACT_PATTERNS_PROMPT = """\
You are a behavioral pattern detector for Enki, an AI assistant. \
Given a conversation transcript, extract behavioral patterns — recurring habits, \
routines, and work style observations about the user.

What belongs here:
- Work habits ("User always checks git status before committing")
- Scheduling preferences ("User prefers meetings in the afternoon")
- Workflow patterns ("User reviews diffs before every commit")
- Time/routine patterns ("User usually works on personal-assistant on weekends")
- Response patterns ("User ignores briefing items about audit verification")
- Communication style ("User prefers bullet points over prose")

What does NOT belong:
- Explicit preferences (those are FACTS, not patterns — e.g. "User prefers dark mode")
- One-off behaviors
- Facts about the user's life context
- Anything transient or session-specific

If no behavioral patterns were observed this session, output nothing.
Maximum 10 patterns. One pattern per line, no bullets or numbering.

Transcript:
{transcript}
"""

_MERGE_PATTERNS_PROMPT = """\
You are a behavioral pattern manager for Enki, an AI assistant. \
Merge the existing behavioral patterns with newly observed patterns.

Rules:
- Keep only behavioral observations (habits, routines, work style)
- Remove duplicates — if a new pattern updates an old one, keep the newer version
- Merge closely related patterns into one
- Be conservative — when in doubt, keep the pattern
- Maximum 50 patterns total
- Output one pattern per line, no bullets or numbering, no headers

EXISTING PATTERNS:
{existing_patterns}

NEW PATTERNS FROM THIS SESSION:
{new_patterns}
"""

_CLEAN_PATTERNS_PROMPT = """\
You are a behavioral pattern curator for Enki, an AI assistant. \
Review the existing behavioral patterns and prune/merge them.

Rules:
- Remove patterns that are clearly outdated or contradicted by newer patterns
- Merge closely related patterns into one
- Remove duplicates
- Be conservative — when in doubt, keep the pattern
- Maximum 50 patterns total
- Output one pattern per line, no bullets or numbering, no headers

EXISTING PATTERNS:
{existing_patterns}
"""

_EXTRACT_FOLLOWUPS_PROMPT = """\
You are a follow-up detector for Enki, an AI assistant. \
Given a conversation transcript, extract any follow-up items — things the user \
is waiting on, needs to check back on, or is expecting a response about.

Look for phrases like:
- "waiting on", "follow up with", "need to check back"
- "will hear back", "expecting a response", "pending from"
- "need to remind", "check with X about Y"

For each follow-up, output one line in this format:
item description|person name (or empty if no specific person)

Examples:
Waiting on John for API review|John
Need to follow up on insurance claim|
Expecting contract from legal team|legal team

If no follow-ups were mentioned, output nothing.
Maximum 5 follow-ups per session.

Transcript:
{transcript}
"""

_CLEAN_PROMPT = """\
You are a memory curator for Enki, an AI assistant. \
Review the existing user facts and prune/merge them.

Rules:
- Remove facts that are clearly outdated or no longer accurate
- Merge closely related facts into one (e.g. two facts about the same preference)
- Remove duplicates
- Be conservative — when in doubt, keep the fact
- NEVER remove facts about named people (family members, partner, pets) — these are permanent
- Maximum 100 facts total
- Output one fact per line, no bullets or numbering, no headers

EXISTING FACTS:
{existing_facts}
"""

_CLEANUP_MARKER = ".last_cleanup"
_CLEANUP_MIN_LINES = 30
_CLEANUP_INTERVAL_DAYS = 7


class MemoryCompactor:
    def __init__(
        self,
        store: MemoryStore,
        anthropic_client: anthropic.AsyncAnthropic,
        facts_path: Path,
        model: str = ModelId.HAIKU,
        patterns_path: Path | None = None,
    ) -> None:
        self._store = store
        self._client = anthropic_client
        self._facts_path = facts_path
        self._model = model
        self._patterns_path = patterns_path
        self._followup_callback: Callable[..., Awaitable[None]] | None = None

    def set_followup_callback(
        self,
        callback: Callable[..., Awaitable[None]],
    ) -> None:
        """Register an async callback that fires for each extracted follow-up."""
        self._followup_callback = callback

    async def compact_session(self, session_id: str) -> list[str]:
        """Distill session turns into durable user facts, merge with existing facts.md."""
        turns = self._store.get_recent_turns(session_id, limit=200)
        if not turns:
            return []

        transcript = "\n".join(f"{t['role'].upper()}: {t['content']}" for t in turns)

        log.info("compacting_session", session_id=session_id, turn_count=len(turns))

        # Step 1: Extract candidate facts from this session
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=512,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(transcript=transcript)}],
        )
        first = response.content[0] if response.content else None
        raw = first.text if isinstance(first, TextBlock) else ""
        new_facts = [line.strip() for line in raw.splitlines() if line.strip()]

        if not new_facts:
            log.info("no_new_facts", session_id=session_id)
            return []

        # Step 2: Read existing facts.md (if any) and merge
        existing_facts = ""
        if self._facts_path.exists():
            existing_facts = self._facts_path.read_text().strip()

        if existing_facts:
            # Merge + deduplicate via haiku
            merge_response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": _MERGE_PROMPT.format(
                            existing_facts=existing_facts,
                            new_facts="\n".join(new_facts),
                        ),
                    }
                ],
            )
            first_merged = merge_response.content[0] if merge_response.content else None
            merged_raw = first_merged.text if isinstance(first_merged, TextBlock) else ""
            merged_facts = [line.strip() for line in merged_raw.splitlines() if line.strip()]
        else:
            merged_facts = new_facts

        # Rewrite facts.md atomically (write to tmp, then rename — crash-safe)
        # Use to_thread to avoid blocking the event loop with synchronous file I/O
        await asyncio.to_thread(self._write_facts, merged_facts)

        # Step 3: Extract behavioral patterns (if patterns_path configured)
        await self._extract_and_merge_patterns(transcript)

        # Step 4: Extract follow-ups
        await self._extract_followups(transcript)

        log.info("compaction_done", session_id=session_id, fact_count=len(merged_facts))
        return merged_facts

    def _write_facts(self, facts: list[str]) -> None:
        """Synchronous helper to write facts atomically — called via to_thread."""
        self._facts_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._facts_path.with_suffix(".tmp")
        with tmp.open("w") as f:
            for fact in facts:
                f.write(f"- {fact}\n")
        tmp.replace(self._facts_path)  # atomic on POSIX

    async def _extract_and_merge_patterns(self, transcript: str) -> None:
        """Extract behavioral patterns from a session transcript and merge with existing."""
        if self._patterns_path is None:
            return

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=512,
            messages=[
                {"role": "user", "content": _EXTRACT_PATTERNS_PROMPT.format(transcript=transcript)},
            ],
        )
        first = response.content[0] if response.content else None
        raw = first.text if isinstance(first, TextBlock) else ""
        new_patterns = [line.strip() for line in raw.splitlines() if line.strip()]

        if not new_patterns:
            log.info("no_new_patterns")
            return

        # Read existing patterns (if any) and merge
        existing_patterns = ""
        if self._patterns_path.exists():
            existing_patterns = self._patterns_path.read_text().strip()

        if existing_patterns:
            merge_response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": _MERGE_PATTERNS_PROMPT.format(
                            existing_patterns=existing_patterns,
                            new_patterns="\n".join(new_patterns),
                        ),
                    },
                ],
            )
            first_merged = merge_response.content[0] if merge_response.content else None
            merged_raw = first_merged.text if isinstance(first_merged, TextBlock) else ""
            merged_patterns = [line.strip() for line in merged_raw.splitlines() if line.strip()]
        else:
            merged_patterns = new_patterns

        # Cap at 50 patterns
        merged_patterns = merged_patterns[:50]

        await asyncio.to_thread(self._write_patterns, merged_patterns)
        log.info("patterns_done", pattern_count=len(merged_patterns))

    def _write_patterns(self, patterns: list[str]) -> None:
        """Synchronous helper to write patterns atomically — called via to_thread."""
        if self._patterns_path is None:
            return
        self._patterns_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._patterns_path.with_suffix(".tmp")
        with tmp.open("w") as f:
            for pattern in patterns:
                f.write(f"- {pattern}\n")
        tmp.replace(self._patterns_path)  # atomic on POSIX

    async def _extract_followups(self, transcript: str) -> None:
        """Extract follow-up items from session and fire callback for each."""
        if self._followup_callback is None:
            return

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=256,
            messages=[
                {"role": "user", "content": _EXTRACT_FOLLOWUPS_PROMPT.format(transcript=transcript)},
            ],
        )
        first = response.content[0] if response.content else None
        raw = first.text if isinstance(first, TextBlock) else ""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]

        for line in lines:
            parts = line.split("|", 1)
            item = parts[0].strip()
            person = parts[1].strip() if len(parts) > 1 else ""
            if item:
                try:
                    await self._followup_callback(item=item, person=person)
                except Exception as exc:
                    log.warning("followup_callback_error", item=item, error=str(exc))

        if lines:
            log.info("followups_extracted", count=len(lines))

    async def clean_patterns(self) -> bool:
        """Prune stale/duplicate patterns from patterns.md.

        Returns True if cleanup ran, False if skipped.
        """
        if self._patterns_path is None or not self._patterns_path.exists():
            return False

        existing_patterns = self._patterns_path.read_text().strip()
        if existing_patterns.count("\n") < 20 - 1:
            return False  # not enough patterns to bother

        log.info("patterns_cleanup_starting", pattern_lines=existing_patterns.count("\n") + 1)
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": _CLEAN_PATTERNS_PROMPT.format(existing_patterns=existing_patterns),
                },
            ],
        )
        first_clean = response.content[0] if response.content else None
        cleaned_raw = first_clean.text if isinstance(first_clean, TextBlock) else ""
        cleaned = [line.strip() for line in cleaned_raw.splitlines() if line.strip()]

        if not cleaned:
            log.warning("patterns_cleanup_empty_result")
            return False

        # Cap at 50
        cleaned = cleaned[:50]

        await asyncio.to_thread(self._write_patterns, cleaned)
        log.info("patterns_cleanup_done", before=existing_patterns.count("\n") + 1, after=len(cleaned))
        return True

    async def clean_facts(self) -> bool:
        """Prune stale/duplicate facts from facts.md.

        Returns True if cleanup ran, False if skipped (not due yet or too few facts).
        Triggered automatically — not user-visible. Runs at most once per 7 days.
        """
        if not self._facts_path.exists():
            return False

        existing_facts = self._facts_path.read_text().strip()
        if existing_facts.count("\n") < _CLEANUP_MIN_LINES - 1:
            return False  # not enough facts to bother

        # Check last cleanup timestamp
        marker = self._facts_path.parent / _CLEANUP_MARKER
        if marker.exists():
            last_run = float(marker.read_text().strip())
            days_since = (time.time() - last_run) / 86400
            if days_since < _CLEANUP_INTERVAL_DAYS:
                log.debug("facts_cleanup_skipped", days_since=round(days_since, 1))
                return False

        log.info("facts_cleanup_starting", facts_lines=existing_facts.count("\n") + 1)
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": _CLEAN_PROMPT.format(existing_facts=existing_facts),
                }
            ],
        )
        first_clean = response.content[0] if response.content else None
        cleaned_raw = first_clean.text if isinstance(first_clean, TextBlock) else ""
        cleaned = [line.strip() for line in cleaned_raw.splitlines() if line.strip()]

        if not cleaned:
            log.warning("facts_cleanup_empty_result")
            return False

        def _write_and_mark() -> None:
            tmp = self._facts_path.with_suffix(".tmp")
            with tmp.open("w") as f:
                for fact in cleaned:
                    f.write(f"- {fact}\n")
            tmp.replace(self._facts_path)
            marker.write_text(str(time.time()))

        await asyncio.to_thread(_write_and_mark)
        log.info("facts_cleanup_done", before=existing_facts.count("\n") + 1, after=len(cleaned))
        return True
