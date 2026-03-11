"""Loop detector — blocks same tool+params repeated N times in a session."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


class LoopDetectorHook:
    name = "loop_detector"

    def __init__(self, threshold: int = 3) -> None:
        self._threshold = threshold
        self._current_session: str = ""
        # session_id -> {(tool_name, params_hash): count}
        self._counts: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)

    def set_session(self, session_id: str) -> None:
        # Purge stale sessions to prevent memory leak (M-02)
        old_keys = [k for k in self._counts if k != session_id]
        for k in old_keys:
            del self._counts[k]
        self._current_session = session_id

    def on_user_message(self) -> None:
        """Reset per-turn counts on each new user message."""
        self._counts[self._current_session].clear()

    async def check(
        self, tool_name: str, params: dict[str, Any]
    ) -> tuple[bool, str | None]:
        params_hash = hashlib.sha256(
            json.dumps(params, sort_keys=True).encode()
        ).hexdigest()[:16]
        key = (tool_name, params_hash)
        session_counts = self._counts[self._current_session]
        count = session_counts.get(key, 0) + 1
        session_counts[key] = count
        if count >= self._threshold:
            return False, (
                f"Tool '{tool_name}' called with identical params {count} times "
                "— possible infinite loop"
            )
        return True, None
