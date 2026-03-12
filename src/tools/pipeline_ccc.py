"""Synchronous Claude Code wrapper for pipeline sub-agents.

Unlike RunClaudeCodeTool (fire-and-forget, cooldown, job registry),
this tool awaits the subprocess and returns stdout directly.
No cooldown — the blocking nature is the natural throttle.

SECURITY NOTE: This is one of the files permitted to use subprocess.
The binary and flags are hardcoded — only the task string is variable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from src.tools.claude_code import _build_claude_md

log = structlog.get_logger()

_CLAUDE_BIN = "claude"
_CLAUDE_FLAGS = ["--dangerously-skip-permissions", "-p"]
_TIMEOUT = 600  # 10 min


class PipelineCCCTool:
    """Sync CCC for pipeline sub-agents — blocks until done, returns output."""

    name = "run_code_task"
    description = (
        "Run Claude Code on a task in the workspace. "
        "Blocks until complete and returns the full output. "
        "Use for reading/writing code, running tests, inspecting files, etc."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Detailed task for Claude Code to execute in the workspace",
            },
        },
        "required": ["task"],
    }

    def __init__(
        self,
        workspace_path: str,
        language: str = "",
        timeout_seconds: int = _TIMEOUT,
    ) -> None:
        self._workspace_path = workspace_path
        self._language = language
        self._timeout_seconds = timeout_seconds

    async def execute(self, **kwargs: Any) -> str:
        task: str = kwargs.get("task", "")
        if not task:
            return "[ERROR] task is required."

        # Inject temp CLAUDE.md if workspace doesn't have one
        claude_md_path: Path | None = None
        ws_path = Path(self._workspace_path)
        candidate = ws_path / "CLAUDE.md"
        if not candidate.exists():
            claude_md_path = candidate
            claude_md_path.write_text(_build_claude_md(self._language))

        cmd = [_CLAUDE_BIN, *_CLAUDE_FLAGS, task]
        try:
            proc = await asyncio.create_subprocess_exec(  # noqa: S603
                *cmd,
                cwd=self._workspace_path,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            log.error("pipeline_ccc_spawn_failed", error=str(exc))
            self._cleanup_claude_md(claude_md_path)
            return f"[ERROR] Failed to start Claude Code: {exc}"

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout_seconds)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            log.error("pipeline_ccc_timeout")
            self._cleanup_claude_md(claude_md_path)
            return f"[TIMEOUT] Claude Code exceeded {self._timeout_seconds // 60} minutes."
        finally:
            self._cleanup_claude_md(claude_md_path)

        output = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()

        if proc.returncode != 0:
            detail = (err or output)[:800]
            log.error("pipeline_ccc_error", returncode=proc.returncode)
            return f"[ERROR] Claude Code exited {proc.returncode}:\n{detail}"

        return output or "Task completed (no output)."

    @staticmethod
    def _cleanup_claude_md(path: Path | None) -> None:
        if path is not None and path.exists():
            path.unlink()
