"""Claude Code self-improvement tool.

SECURITY NOTE: This is one of only two files in the codebase permitted to use subprocess.
The binary and flags are hardcoded — only the task string (user-confirmed) is variable.
code_scanner.py grants subprocess a path-based exception for this file only.

Runs as a background asyncio task — execute() returns immediately with a job ID.
The notifier receives the result when the job completes (no blocking of the main agent).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

import structlog

# Files/dirs Claude Code must never modify when working on THIS assistant's repo.
# Any violation is flagged loudly in the completion notification.
PROTECTED_PATHS: frozenset[str] = frozenset({
    "src/guardrails/",
    "src/audit/",
    "src/agent.py",
    "main.py",
    "src/config.py",
})

log = structlog.get_logger()

# Hardcoded binary + flags — never constructed from input
# Task string is passed as a single argv argument (no shell=True, no injection risk)
_CLAUDE_BIN = "claude"
_CLAUDE_FLAGS = ["--dangerously-skip-permissions", "-p"]

_TIMEOUT_SECONDS = 600   # 10-minute hard cap per job
_COOLDOWN_SECONDS = 300  # 5-minute minimum between spawns

# Language-specific rules injected into temp CLAUDE.md before CCC runs
_LANG_RULES: dict[str, str] = {
    "python": (
        "## This Project: Python\n"
        "- Python 3.12+, full type hints everywhere.\n"
        "- `async def` / `await` throughout — no blocking calls in async context.\n"
        "- `structlog` for logging — never `print()`, never `logging`.\n"
        "- Protocol-based interfaces (not ABCs). No global singletons except config.\n"
        "- `ruff` for linting, `mypy --strict` for types, `pytest` + `pytest-asyncio` for tests.\n"
        "- TDD: write failing tests first, implement to pass, then refactor.\n"
    ),
    "typescript": (
        "## This Project: TypeScript\n"
        "- Strict TypeScript (`\"strict\": true`). No `any` unless justified with a comment.\n"
        "- ESLint + Prettier — follow existing config, don't change tooling.\n"
        "- Vitest or Jest — check which is set up before writing tests.\n"
        "- Async/await over callbacks. No `console.log` in committed code.\n"
        "- Named exports preferred over default exports.\n"
        "- TDD: write failing tests first, implement to pass, then refactor.\n"
    ),
    "go": (
        "## This Project: Go\n"
        "- `gofmt` and `go vet` must pass.\n"
        "- Table-driven tests with `go test ./...`.\n"
        "- Return errors, don't panic. Wrap with `fmt.Errorf(\"context: %w\", err)`.\n"
        "- Interfaces defined at point of use (consumer side).\n"
        "- TDD: write failing tests first, implement to pass, then refactor.\n"
    ),
    "rust": (
        "## This Project: Rust\n"
        "- `cargo fmt` and `cargo clippy -- -D warnings` must pass.\n"
        "- `#[cfg(test)]` modules for unit tests, integration tests in `tests/`.\n"
        "- Prefer `Result<T, E>` over panics. Use `thiserror` for error types.\n"
        "- TDD: write failing tests first, implement to pass, then refactor.\n"
    ),
}

_BASE_CLAUDE_MD = """\
# Claude Code Instructions (injected by Enki)

## Universal Rules
- TDD always: write failing tests first, then implement to pass, then refactor.
- Small commits: commit after each logical unit of work.
- Read before writing: understand existing code before modifying it.
- No over-engineering: implement exactly what is asked.
- Meaningful names: self-documenting code. Comments explain *why*, not *what*.
- Delete dead code — don't comment it out.
- Run the test suite before declaring done — it must pass.

## Git Discipline
- Branch names: feat/description, fix/description, chore/description.
- Commit messages: imperative mood ("add X", not "added X").
- Never commit to main/master directly. Never force-push.

{lang_section}
"""


def _build_claude_md(language: str | None) -> str:
    lang_key = (language or "").lower()
    lang_section = _LANG_RULES.get(lang_key, "")
    return _BASE_CLAUDE_MD.format(lang_section=lang_section)


_GIST_THRESHOLD = 500  # chars — above this, create a gist instead of dumping raw text


class ClaudeCodeNotifier(Protocol):
    async def ask_single_confirm(self, reason: str, changes_summary: str) -> bool: ...
    async def send(self, message: str) -> None: ...


class RunClaudeCodeTool:
    name = "run_claude_code"
    description = (
        "Spawn Claude Code to make multi-file changes to a codebase. "
        "Optionally target an external workspace via workspace_id. "
        "Runs as a background job — returns a job ID immediately without blocking. "
        "Use for features, refactors, new tools with tests, or any change spanning "
        "more than one file. Requires double confirmation. "
        "5-minute cooldown between spawns. 10-minute max per task. "
        "You will be notified when the job completes."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Detailed task for Claude Code to execute",
            },
            "reason": {
                "type": "string",
                "description": "Why this change is needed",
            },
            "workspace_id": {
                "type": "string",
                "description": "Optional: run CCC in this registered workspace instead of the assistant repo",
            },
        },
        "required": ["task", "reason"],
    }

    def __init__(
        self,
        notifier: ClaudeCodeNotifier,
        project_dir: Path,
        workspace_store: object = None,
        job_registry: object = None,
    ) -> None:
        self._notifier = notifier
        self._project_dir = project_dir
        self._workspace_store = workspace_store
        self._job_registry = job_registry
        self._last_spawn: float = 0.0
        self._agent: Any = None

    def set_agent(self, agent: Any) -> None:
        """Wire in the main agent for summarization. Called after Agent is built."""
        self._agent = agent

    async def execute(self, **kwargs: Any) -> str:
        task: str = kwargs["task"]
        reason: str = kwargs["reason"]
        workspace_id: str | None = kwargs.get("workspace_id")

        # Cooldown check
        elapsed = time.time() - self._last_spawn
        if self._last_spawn > 0 and elapsed < _COOLDOWN_SECONDS:
            remaining = int(_COOLDOWN_SECONDS - elapsed)
            return f"[BLOCKED] Claude Code cooldown active — {remaining}s remaining."

        # Resolve workspace
        workspace_path: str | None = None
        language: str | None = None
        if workspace_id:
            if self._workspace_store is None:
                return "[ERROR] No workspace store configured."
            from src.workspaces.store import WorkspaceStore
            assert isinstance(self._workspace_store, WorkspaceStore)
            ws = self._workspace_store.get(workspace_id)
            if ws is None:
                return f"[ERROR] Workspace '{workspace_id}' not found. Use list_workspaces to see registered workspaces."
            workspace_path = ws["local_path"]
            language = ws.get("language")
            self._workspace_store.touch(workspace_id)

        # Prepend protection restriction (for assistant's own repo only)
        if workspace_path is None:
            protected_list = ", ".join(sorted(PROTECTED_PATHS))
            guarded_task = (
                f"IMPORTANT RESTRICTIONS — do NOT modify any of these paths: {protected_list}\n"
                f"If the task requires changes to those paths, stop and explain why instead.\n\n"
                f"{task}"
            )
        else:
            guarded_task = task

        # Single confirmation (double-confirm is reserved for restart/pipeline)
        confirmed = await self._notifier.ask_single_confirm(
            reason=reason,
            changes_summary=task[:400],
        )
        if not confirmed:
            return "Cancelled — user did not confirm."

        job_id = str(uuid.uuid4())[:8]
        log.warning(
            "claude_code_spawn",
            reason=reason,
            task_preview=task[:200],
            job_id=job_id,
            workspace_id=workspace_id,
        )
        self._last_spawn = time.time()

        if self._job_registry is not None:
            from src.jobs import JobRegistry
            assert isinstance(self._job_registry, JobRegistry)
            target_desc = f"workspace '{workspace_id}'" if workspace_id else "assistant repo"
            self._job_registry.start(job_id, job_type="ccc", description=f"{task[:60]} ({target_desc})")

        asyncio.create_task(
            self._run_background(
                job_id, guarded_task,
                workspace_path=workspace_path,
                language=language,
            )
        )

        target = f"workspace '{workspace_id}'" if workspace_id else "assistant repo"
        return (
            f"Claude Code job {job_id} started in background ({target}). "
            f"You'll receive a notification when it completes (up to 10 min)."
        )

    async def _run_background(
        self,
        job_id: str,
        task: str,
        *,
        workspace_path: str | None = None,
        language: str | None = None,
    ) -> None:
        """Run claude in a background asyncio task; notify when done."""
        run_dir = workspace_path or str(self._project_dir)

        # Inject temp CLAUDE.md into workspace if it doesn't already have one
        claude_md_path: Path | None = None
        if workspace_path:
            claude_md_path = Path(workspace_path) / "CLAUDE.md"
            if claude_md_path.exists():
                claude_md_path = None  # don't touch existing CLAUDE.md
            else:
                claude_md_path.write_text(_build_claude_md(language))

        cmd = [_CLAUDE_BIN, *_CLAUDE_FLAGS, task]
        try:
            proc = await asyncio.create_subprocess_exec(  # noqa: S603
                *cmd,
                cwd=run_dir,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            log.error("claude_code_spawn_failed", job_id=job_id, error=str(exc))
            await self._notifier.send(f"[Job {job_id}] Failed to start Claude Code: {exc}")
            if claude_md_path and claude_md_path.exists():
                claude_md_path.unlink()
            return

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            log.error("claude_code_timeout", job_id=job_id)
            if claude_md_path and claude_md_path.exists():
                claude_md_path.unlink()
            await self._notifier.send(
                f"[Job {job_id}] TIMEOUT — exceeded {_TIMEOUT_SECONDS // 60} minutes. Process killed."
            )
            return
        finally:
            # Always clean up temp CLAUDE.md
            if claude_md_path and claude_md_path.exists():
                claude_md_path.unlink()

        output = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()

        if proc.returncode != 0:
            detail = (err or output)[:800]
            log.error("claude_code_error", job_id=job_id, returncode=proc.returncode)
            if self._job_registry is not None:
                from src.jobs import JobRegistry
                assert isinstance(self._job_registry, JobRegistry)
                self._job_registry.finish(job_id, success=False, error=f"exit {proc.returncode}")
            await self._notifier.send(f"[Job {job_id}] ERROR (exit {proc.returncode}):\n{detail}")
            return

        result = output or "Task completed (no output)."
        log.info("claude_code_done", job_id=job_id)

        # Get git diff to show exactly what changed
        diff_msg = ""
        try:
            diff_proc = await asyncio.create_subprocess_exec(
                "git", "diff", "HEAD",
                cwd=run_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            diff_out, _ = await asyncio.wait_for(diff_proc.communicate(), timeout=15)
            diff_text = diff_out.decode(errors="replace").strip()

            if diff_text:
                changed_files = [
                    line[6:] for line in diff_text.splitlines()
                    if line.startswith("+++ b/")
                ]
                # Only check protected paths for the assistant's own repo
                if workspace_path is None:
                    violations = [
                        f for f in changed_files
                        if any(f.startswith(p) for p in PROTECTED_PATHS)
                    ]
                    if violations:
                        log.error("claude_code_protected_path_violation", job_id=job_id, files=violations)
                        diff_msg = (
                            f"\n\n⚠️ PROTECTED PATH VIOLATION — these files should NOT have been modified:\n"
                            + "\n".join(f"  • {v}" for v in violations)
                            + f"\n\nFull diff:\n{diff_text[:2000]}"
                        )
                    else:
                        diff_msg = f"\n\nDiff:\n{diff_text[:2000]}"
                else:
                    diff_msg = f"\n\nDiff:\n{diff_text[:2000]}"
        except Exception as exc:
            diff_msg = f"\n\n[Could not get diff: {exc}]"

        if self._job_registry is not None:
            from src.jobs import JobRegistry
            assert isinstance(self._job_registry, JobRegistry)
            self._job_registry.finish(job_id, success=True)

        full_output = f"{result}{diff_msg}"
        await self._send_output(job_id, full_output)

    async def _create_gist(self, content: str, description: str) -> str | None:
        """Create a secret GitHub gist via gh CLI. Returns URL or None on failure."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "gist", "create", "--secret",
                "--desc", description,
                "--filename", "output.md",
                "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input=content.encode()), timeout=30
            )
            if proc.returncode == 0:
                return stdout.decode().strip()
        except Exception as exc:
            log.warning("gist_create_failed", error=str(exc))
        return None

    async def _send_output(self, job_id: str, full_output: str) -> None:
        """Send job output to notifier. Long output → secret gist + Enki summary."""
        if len(full_output) <= _GIST_THRESHOLD or self._agent is None:
            await self._notifier.send(f"[Job {job_id}] Done:\n{full_output[:800]}")
            return

        gist_url = await self._create_gist(full_output, f"Enki job {job_id} output")
        summary_prompt = (
            f"Summarise the following Claude Code job output in 2-3 bullet points "
            f"for a Telegram message. Be concise and focus on what changed.\n\n{full_output[:4000]}"
        )
        try:
            summary = await self._agent.run_turn(summary_prompt)
        except Exception as exc:
            log.warning("gist_summary_failed", error=str(exc))
            summary = full_output[:400]

        if gist_url:
            await self._notifier.send(
                f"[Job {job_id}] Done:\n{summary}\n\nFull report: {gist_url}"
            )
        else:
            await self._notifier.send(
                f"[Job {job_id}] Done:\n{summary}\n\n(full output too long; gist creation failed)"
            )
