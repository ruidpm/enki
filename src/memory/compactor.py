"""Post-session memory compactor — distills conversation into durable user facts."""
from __future__ import annotations

import time
from pathlib import Path

import anthropic
import structlog

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
- Maximum 100 facts total
- Output one fact per line, no bullets or numbering, no headers

EXISTING FACTS:
{existing_facts}

NEW FACTS FROM THIS SESSION:
{new_facts}
"""

_CLEAN_PROMPT = """\
You are a memory curator for Enki, an AI assistant. \
Review the existing user facts and prune/merge them.

Rules:
- Remove facts that are clearly outdated or no longer accurate
- Merge closely related facts into one (e.g. two facts about the same preference)
- Remove duplicates
- Be conservative — when in doubt, keep the fact
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
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        self._store = store
        self._client = anthropic_client
        self._facts_path = facts_path
        self._model = model

    async def compact_session(self, session_id: str) -> list[str]:
        """Distill session turns into durable user facts, merge with existing facts.md."""
        turns = self._store.get_recent_turns(session_id, limit=200)
        if not turns:
            return []

        transcript = "\n".join(
            f"{t['role'].upper()}: {t['content']}" for t in turns
        )

        log.info("compacting_session", session_id=session_id, turn_count=len(turns))

        # Step 1: Extract candidate facts from this session
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=512,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(transcript=transcript)}],
        )
        raw = response.content[0].text if response.content else ""
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
                messages=[{
                    "role": "user",
                    "content": _MERGE_PROMPT.format(
                        existing_facts=existing_facts,
                        new_facts="\n".join(new_facts),
                    ),
                }],
            )
            merged_raw = merge_response.content[0].text if merge_response.content else ""
            merged_facts = [line.strip() for line in merged_raw.splitlines() if line.strip()]
        else:
            merged_facts = new_facts

        # Rewrite facts.md atomically (write to tmp, then rename — crash-safe)
        self._facts_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._facts_path.with_suffix(".tmp")
        with tmp.open("w") as f:
            for fact in merged_facts:
                f.write(f"- {fact}\n")
        tmp.replace(self._facts_path)  # atomic on POSIX

        log.info("compaction_done", session_id=session_id, fact_count=len(merged_facts))
        return merged_facts

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
            messages=[{
                "role": "user",
                "content": _CLEAN_PROMPT.format(existing_facts=existing_facts),
            }],
        )
        cleaned_raw = response.content[0].text if response.content else ""
        cleaned = [line.strip() for line in cleaned_raw.splitlines() if line.strip()]

        if not cleaned:
            log.warning("facts_cleanup_empty_result")
            return False

        tmp = self._facts_path.with_suffix(".tmp")
        with tmp.open("w") as f:
            for fact in cleaned:
                f.write(f"- {fact}\n")
        tmp.replace(self._facts_path)

        marker.write_text(str(time.time()))
        log.info("facts_cleanup_done", before=existing_facts.count("\n") + 1, after=len(cleaned))
        return True
