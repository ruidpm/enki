"""Shared output delivery — gist creation + summarized notifications.

Used by RunClaudeCodeTool and RunPipelineTool to avoid duplication.
Summarization uses a stateless Anthropic API call (no conversation pollution).
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.interfaces.notifier import Notifier

if TYPE_CHECKING:
    import anthropic

    from src.jobs import JobRegistry

log = structlog.get_logger()

_GIST_THRESHOLD = 500  # chars — above this, create a gist instead of dumping raw text


class OutputDelivery:
    """Create gists and send summarized output via a notifier."""

    def __init__(
        self,
        notifier: Notifier,
        anthropic_client: Any | None = None,
        model: str = "",
        gist_threshold: int = _GIST_THRESHOLD,
        job_registry: Any | None = None,
    ) -> None:
        self._notifier = notifier
        self._client: anthropic.AsyncAnthropic | None = anthropic_client
        self._model = model
        self._gist_threshold = gist_threshold
        self._job_registry: JobRegistry | None = job_registry

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

    async def create_multi_file_gist(self, files: dict[str, str], description: str) -> str | None:
        """Create a multi-file gist. files = {filename: content}. Returns URL or None."""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                file_paths: list[str] = []
                for filename, content in files.items():
                    p = Path(tmpdir) / filename
                    p.write_text(content)
                    file_paths.append(str(p))

                proc = await asyncio.create_subprocess_exec(
                    "gh",
                    "gist",
                    "create",
                    "--desc",
                    description,
                    *file_paths,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode == 0:
                    return stdout.decode().strip()
                log.warning(
                    "multi_gist_create_failed",
                    returncode=proc.returncode,
                    stderr=stderr.decode().strip()[:300],
                )
        except Exception as exc:
            log.warning("multi_gist_create_failed", error=str(exc))
        return None

    async def _summarize(self, text: str, context: str) -> str | None:
        """Stateless summarization via Anthropic API. Returns summary or None."""
        if self._client is None:
            return None
        prompt = (
            f"Summarise the following output in 2-3 bullet points for a Telegram message. Be concise.{context}\n\n{text[:4000]}"
        )
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text  # type: ignore[union-attr]
        except Exception as exc:
            log.warning("output_summary_failed", error=str(exc))
            return None

    async def send_output(
        self,
        job_id: str,
        full_output: str,
        *,
        prefix: str = "",
        summary_context: str = "",
    ) -> None:
        """Send job output to notifier. Long output -> gist + stateless summary."""
        try:
            if len(full_output) <= self._gist_threshold or self._client is None:
                truncated = full_output[: max(self._gist_threshold + 300, 800)]
                await self._notifier.send(f"{prefix}\n{truncated}")
                return

            gist_url = await self.create_gist(full_output, f"Enki job {job_id} output")
            summary = await self._summarize(full_output, summary_context)
            if summary is None:
                summary = full_output[:400]

            # Store results in registry if available
            if self._job_registry is not None:
                self._job_registry.set_result(job_id, summary=summary, gist_url=gist_url)

            if gist_url:
                await self._notifier.send(f"{prefix}\n{summary}\n\nFull report: {gist_url}")
            else:
                await self._notifier.send(f"{prefix}\n{summary}\n\n(full output too long; gist creation failed)")
        except Exception as exc:
            log.error("output_delivery_failed", job_id=job_id, error=str(exc))
