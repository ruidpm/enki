"""Shared output delivery — gist creation + summarized notifications.

Used by RunClaudeCodeTool and RunPipelineTool to avoid duplication.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from src.interfaces.notifier import Notifier

log = structlog.get_logger()

_GIST_THRESHOLD = 500  # chars — above this, create a gist instead of dumping raw text


class OutputDelivery:
    """Create gists and send summarized output via a notifier."""

    def __init__(
        self,
        notifier: Notifier,
        agent: Any = None,
        gist_threshold: int = _GIST_THRESHOLD,
    ) -> None:
        self._notifier = notifier
        self._agent = agent
        self._gist_threshold = gist_threshold

    def set_agent(self, agent: Any) -> None:
        """Wire agent after construction (avoids circular deps)."""
        self._agent = agent

    async def create_gist(self, content: str, description: str) -> str | None:
        """Create a secret GitHub gist via gh CLI. Returns URL or None on failure."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "gist",
                "create",
                "--desc",
                description,
                "--filename",
                "output.md",
                "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(input=content.encode()), timeout=30)
            if proc.returncode == 0:
                return stdout.decode().strip()
            log.warning(
                "gist_create_failed",
                returncode=proc.returncode,
                stderr=stderr.decode().strip()[:300],
            )
        except Exception as exc:
            log.warning("gist_create_failed", error=str(exc))
        return None

    async def send_output(
        self,
        job_id: str,
        full_output: str,
        *,
        prefix: str = "",
        summary_context: str = "",
    ) -> None:
        """Send job output to notifier. Long output -> gist + agent summary."""
        try:
            if len(full_output) <= self._gist_threshold or self._agent is None:
                truncated = full_output[: max(self._gist_threshold + 300, 800)]
                await self._notifier.send(f"{prefix}\n{truncated}")
                return

            gist_url = await self.create_gist(full_output, f"Enki job {job_id} output")
            prompt = (
                f"Summarise the following output in 2-3 bullet points "
                f"for a Telegram message. Be concise.{summary_context}"
                f"\n\n{full_output[:4000]}"
            )
            try:
                summary = await self._agent.run_turn(prompt)
            except Exception as exc:
                log.warning("output_summary_failed", job_id=job_id, error=str(exc))
                summary = full_output[:400]

            if gist_url:
                await self._notifier.send(f"{prefix}\n{summary}\n\nFull report: {gist_url}")
            else:
                await self._notifier.send(f"{prefix}\n{summary}\n\n(full output too long; gist creation failed)")
        except Exception as exc:
            log.error("output_delivery_failed", job_id=job_id, error=str(exc))
