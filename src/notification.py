"""SmartNotifier — priority classification and quiet hours for notifications."""

from __future__ import annotations

import enum
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from src.interfaces.notifier import Notifier

log = structlog.get_logger()

# Pre-compiled patterns for classification
_URGENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bconfirm\b", re.IGNORECASE),
    re.compile(r"\bapprove\b", re.IGNORECASE),
    re.compile(r"\bproceed\b", re.IGNORECASE),
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bfailed\b", re.IGNORECASE),
    re.compile(r"\bcrashed\b", re.IGNORECASE),
    re.compile(r"\b90%"),
    re.compile(r"\bbudget\b", re.IGNORECASE),
]

_LOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b80%"),
    re.compile(r"^FYI\b", re.IGNORECASE),
    re.compile(r"^Info\b", re.IGNORECASE),
]


class Priority(enum.Enum):
    URGENT = "urgent"
    NORMAL = "normal"
    LOW = "low"


class SmartNotifier:
    """Wraps a Notifier with priority classification and quiet-hours queuing.

    - URGENT: always delivered immediately (errors, confirmations, high cost alerts).
    - NORMAL: delivered immediately unless quiet hours — then queued.
    - LOW: queued during quiet hours, delivered immediately otherwise.

    All interactive methods (ask_confirm, ask_free_text, etc.) pass through directly.
    """

    def __init__(
        self,
        inner: Notifier,
        *,
        quiet_start: int = 22,
        quiet_end: int = 8,
        timezone: str = "UTC",
    ) -> None:
        self._inner = inner
        self._quiet_start = quiet_start
        self._quiet_end = quiet_end
        self._tz = ZoneInfo(timezone)
        self._queue: list[str] = []

    # --- Priority classification (deterministic, no LLM) ---

    def _classify(self, message: str) -> Priority:
        """Classify a message into URGENT, NORMAL, or LOW priority."""
        for pattern in _URGENT_PATTERNS:
            if pattern.search(message):
                return Priority.URGENT
        for pattern in _LOW_PATTERNS:
            if pattern.search(message):
                return Priority.LOW
        return Priority.NORMAL

    # --- Quiet hours ---

    def _now_hour(self) -> int:
        """Current hour in the configured timezone. Overridable for testing."""
        return datetime.now(tz=self._tz).hour

    def _is_quiet_hours(self) -> bool:
        """Check if current time is within quiet hours."""
        if self._quiet_start == self._quiet_end:
            return False
        hour = self._now_hour()
        if self._quiet_start > self._quiet_end:
            # Wraps midnight: e.g. 22:00-08:00
            return hour >= self._quiet_start or hour < self._quiet_end
        else:
            # Same day: e.g. 01:00-06:00
            return self._quiet_start <= hour < self._quiet_end

    # --- Notifier protocol: send (with priority/quiet hours logic) ---

    async def send(self, message: str) -> None:
        priority = self._classify(message)
        if priority == Priority.URGENT or not self._is_quiet_hours():
            await self._inner.send(message)
        else:
            self._queue.append(message)
            log.debug("notification_queued", priority=priority.value, queue_size=len(self._queue))

    # --- Flush queued messages ---

    async def flush_queue(self) -> None:
        """Send all queued messages as one batched notification, then clear the queue."""
        if not self._queue:
            return
        batched = "\n---\n".join(self._queue)
        self._queue.clear()
        log.info("notification_queue_flushed", message_count=len(self._queue) + 1)
        await self._inner.send(batched)

    # --- Notifier protocol: interactive methods (always pass through) ---

    async def ask_confirm(self, tool_name: str, params: dict[str, Any]) -> bool:
        return await self._inner.ask_confirm(tool_name, params)

    async def ask_single_confirm(self, reason: str, changes_summary: str) -> bool:
        return await self._inner.ask_single_confirm(reason, changes_summary)

    async def ask_double_confirm(self, reason: str, changes_summary: str) -> bool:
        return await self._inner.ask_double_confirm(reason, changes_summary)

    async def ask_free_text(self, prompt: str, timeout_s: int = 300) -> str | None:
        return await self._inner.ask_free_text(prompt, timeout_s=timeout_s)

    async def ask_scope_approval(self, prompt: str, timeout_s: int = 600) -> str | None:
        return await self._inner.ask_scope_approval(prompt, timeout_s=timeout_s)

    async def send_diff(self, tool_name: str, description: str, code: str, code_hash: str) -> None:
        await self._inner.send_diff(tool_name, description, code, code_hash)

    async def wait_for_approval(self, tool_name: str) -> bool:
        return await self._inner.wait_for_approval(tool_name)
