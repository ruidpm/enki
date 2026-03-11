"""Autonomous pipeline orchestrator.

RunPipelineTool — the "COO" tool. You say "build X for workspace Y", Enki runs
the full RESEARCH → SCOPE → PLAN → IMPLEMENT → TEST → REVIEW → PR pipeline
autonomously as a background job.

Design:
- Double confirm upfront (one gate before anything runs)
- Each stage runs with the appropriate team via SubAgentRunner
- IMPLEMENT uses Claude Code CLI directly (same as RunClaudeCodeTool)
- PR stage: git push branch + create PR via gh CLI
- Artifacts saved after each stage; pipeline store tracks progress
- User gets a notification after each stage + a final summary with PR URL
- Creates PR but never merges — the human always controls the merge button
"""
from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any, Protocol

import structlog

from src.pipeline.store import PipelineStage, PipelineStatus, PipelineStore
from src.sub_agent import SubAgentRunner
from src.teams.store import TeamsStore
from src.workspaces.store import WorkspaceStore

log = structlog.get_logger()

# Hardcoded CCC binary + flags (same as RunClaudeCodeTool)
_CLAUDE_BIN = "claude"
_CLAUDE_FLAGS = ["--dangerously-skip-permissions", "-p"]
_CCC_TIMEOUT = 600  # 10 min

# Map each stage to the team responsible
_STAGE_TEAM: dict[str, str] = {
    PipelineStage.RESEARCH: "researcher",
    PipelineStage.SCOPE: "architect",
    PipelineStage.PLAN: "architect",
    PipelineStage.TEST: "qa",
    PipelineStage.REVIEW: "architect",
}

_STAGE_ARTIFACT_TYPE: dict[str, str] = {
    PipelineStage.RESEARCH: "research_report",
    PipelineStage.SCOPE: "requirements",
    PipelineStage.PLAN: "implementation_plan",
    PipelineStage.IMPLEMENT: "implementation_summary",
    PipelineStage.TEST: "test_results",
    PipelineStage.REVIEW: "review_summary",
    PipelineStage.PR: "pr_url",
}

# Stages handled by SubAgentRunner (text output from team LLM)
_LLM_STAGES = {
    PipelineStage.RESEARCH,
    PipelineStage.SCOPE,
    PipelineStage.PLAN,
    PipelineStage.TEST,
    PipelineStage.REVIEW,
}


_CLARIFICATION_PREFIX = "CLARIFICATION_NEEDED:"
_MAX_CLARIFICATION_ROUNDS = 2


class Notifier(Protocol):
    async def ask_single_confirm(self, reason: str, changes_summary: str) -> bool: ...
    async def send(self, message: str) -> None: ...
    async def ask_free_text(self, prompt: str, timeout_s: int = 300) -> str | None: ...


def _branch_name(task: str, pipeline_id: str) -> str:
    """Derive a safe git branch name from the task description."""
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower())[:40].strip("-")
    return f"feat/{slug}-{pipeline_id}"


async def _run_git(*cmd: str, cwd: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode().strip(), stderr.decode().strip()


class RunPipelineTool:
    name = "run_pipeline"
    description = (
        "Autonomously run a full engineering pipeline for an external workspace. "
        "Stages run in order: RESEARCH → SCOPE → PLAN → IMPLEMENT → TEST → REVIEW → PR. "
        "Enki manages every stage independently — you get notifications as each one completes. "
        "A PR is opened at the end; you review and merge on GitHub. "
        "Requires double confirmation. Returns a pipeline ID immediately."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Workspace to build in (use list_workspaces to see options)",
            },
            "task": {
                "type": "string",
                "description": "What to build — be specific. Include acceptance criteria.",
            },
            "context": {
                "type": "string",
                "description": "Optional extra context (tech stack preferences, constraints, etc.)",
            },
        },
        "required": ["workspace_id", "task"],
    }

    _GIST_THRESHOLD = 500  # chars — above this, create a gist instead of dumping raw text

    def __init__(
        self,
        notifier: Notifier,
        pipeline_store: PipelineStore,
        workspace_store: WorkspaceStore,
        teams_store: TeamsStore,
        config: Any,
        tool_registry: dict[str, Any],
        job_registry: object = None,
    ) -> None:
        self._notifier = notifier
        self._pipelines = pipeline_store
        self._workspaces = workspace_store
        self._teams = teams_store
        self._config = config
        self._registry = tool_registry
        self._job_registry = job_registry
        self._agent: Any = None

    def set_agent(self, agent: Any) -> None:
        """Wire in the main agent for summarization. Called after Agent is built."""
        self._agent = agent

    async def execute(self, **kwargs: Any) -> str:
        workspace_id: str = kwargs.get("workspace_id", "").strip()
        task: str = kwargs.get("task", "").strip()
        context: str = kwargs.get("context", "")

        if not workspace_id:
            return "[ERROR] workspace_id is required."
        if not task:
            return "[ERROR] task is required."

        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            return f"[ERROR] Workspace '{workspace_id}' not found. Use list_workspaces."

        from src.workspaces.store import TrustLevel
        trust_level: int = workspace.get("trust_level", TrustLevel.PROPOSE)
        if trust_level < TrustLevel.PROPOSE:
            return (
                f"[BLOCKED] Workspace '{workspace_id}' is READ_ONLY (trust_level=0). "
                f"Pipeline requires at least PROPOSE trust. "
                f"Use manage_workspace set_trust to elevate."
            )
        if trust_level == TrustLevel.PROPOSE:
            await self._notifier.send(
                f"[Pipeline] Workspace '{workspace['name']}' trust is PROPOSE. "
                f"Pipeline will pause for confirmation before IMPLEMENT and before opening the PR."
            )

        confirmed = await self._notifier.ask_single_confirm(
            reason=f"Run pipeline: {workspace['name']}",
            changes_summary=task[:400],
        )
        if not confirmed:
            return "Cancelled — pipeline not started."

        pipeline_id = uuid.uuid4().hex[:8]
        self._pipelines.create(pipeline_id, workspace_id=workspace_id, task=task)

        log.info("run_pipeline_started", pipeline_id=pipeline_id, workspace_id=workspace_id)

        if self._job_registry is not None:
            from src.jobs import JobRegistry
            assert isinstance(self._job_registry, JobRegistry)
            self._job_registry.start(
                pipeline_id,
                job_type="pipeline",
                description=task[:80],
                model=self._config.haiku_model,
            )

        bg_task = asyncio.create_task(
            self._run_background(
                pipeline_id=pipeline_id,
                task=task,
                workspace=workspace,
                context=context,
            )
        )
        if self._job_registry is not None:
            from src.jobs import JobRegistry
            assert isinstance(self._job_registry, JobRegistry)
            self._job_registry.set_task(pipeline_id, bg_task)

        return (
            f"Pipeline {pipeline_id} started for workspace '{workspace['name']}'. "
            f"Running: RESEARCH → SCOPE → PLAN → IMPLEMENT → TEST → REVIEW → PR. "
            f"You'll get a notification after each stage completes."
        )

    # ------------------------------------------------------------------
    # Background orchestration
    # ------------------------------------------------------------------

    async def _run_background(
        self,
        pipeline_id: str,
        task: str,
        workspace: dict[str, Any],
        context: str = "",
    ) -> None:
        """Run all pipeline stages sequentially. Notify after each one."""
        workspace_path = workspace["local_path"]
        language = workspace.get("language") or ""
        artifacts: dict[str, str] = {}  # stage → content (accumulated for context)

        def _update_stage(stage: str) -> None:
            if self._job_registry is not None:
                from src.jobs import JobRegistry
                assert isinstance(self._job_registry, JobRegistry)
                self._job_registry.update_stage(pipeline_id, stage.upper())

        def _finish_job(success: bool, error: str | None = None) -> None:
            if self._job_registry is not None:
                from src.jobs import JobRegistry
                assert isinstance(self._job_registry, JobRegistry)
                self._job_registry.finish(pipeline_id, success=success, error=error)

        try:
            for stage in PipelineStage.ORDERED:
                _update_stage(stage)

                if stage == PipelineStage.IMPLEMENT:
                    result = await self._run_implement(
                        pipeline_id, task, workspace_path, language, artifacts
                    )
                elif stage == PipelineStage.PR:
                    pr_confirmed = await self._notifier.ask_single_confirm(
                        reason=f"[Pipeline {pipeline_id}] Open pull request?",
                        changes_summary=(
                            f"Task: {task[:200]}\n"
                            f"IMPLEMENT complete. Ready to push branch and open PR."
                        ),
                    )
                    if not pr_confirmed:
                        await self._notifier.send(
                            f"[Pipeline {pipeline_id}] PR skipped — code is on the workspace. "
                            f"Run create_pr manually when ready."
                        )
                        self._pipelines.set_status(pipeline_id, PipelineStatus.COMPLETED)
                        _finish_job(success=True)
                        return
                    result = await self._run_pr(
                        pipeline_id, task, workspace_path, artifacts
                    )
                else:
                    result = await self._run_llm_stage(
                        pipeline_id, stage, task, context, artifacts
                    )

                artifacts[stage] = result
                self._pipelines.save_artifact(
                    pipeline_id, stage, _STAGE_ARTIFACT_TYPE[stage], result
                )
                self._pipelines.advance_stage(
                    pipeline_id,
                    PipelineStage.next(stage) or stage,
                )

                await self._send_stage_output(pipeline_id, stage, result)

        except asyncio.CancelledError:
            log.info("run_pipeline_cancelled", pipeline_id=pipeline_id)
            self._pipelines.set_status(pipeline_id, PipelineStatus.ABORTED)
            _finish_job(success=False, error="Cancelled")
            await self._notifier.send(f"[Pipeline {pipeline_id}] Cancelled.")
            return
        except Exception as exc:
            log.error("run_pipeline_error", pipeline_id=pipeline_id, error=str(exc))
            self._pipelines.set_status(pipeline_id, PipelineStatus.ABORTED)
            _finish_job(success=False, error=str(exc))
            await self._notifier.send(
                f"[Pipeline {pipeline_id}] ERROR — pipeline aborted.\n{exc}"
            )
            return

        self._pipelines.set_status(pipeline_id, PipelineStatus.COMPLETED)
        _finish_job(success=True)
        pr_url = artifacts.get(PipelineStage.PR, "PR creation failed — check logs.")
        await self._notifier.send(
            f"[Pipeline {pipeline_id}] DONE. All stages complete.\n"
            f"PR: {pr_url}\n"
            f"Review and merge when ready."
        )

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
            log.warning("pipeline_gist_create_failed", error=str(exc))
        return None

    async def _send_stage_output(self, pipeline_id: str, stage: str, result: str) -> None:
        """Send stage output to notifier. Long output → secret gist + Enki summary."""
        prefix = f"[Pipeline {pipeline_id}] {stage.upper()} complete."
        if len(result) <= self._GIST_THRESHOLD or self._agent is None:
            await self._notifier.send(f"{prefix}\n\n{result[:600]}")
            return

        gist_url = await self._create_gist(result, f"Pipeline {pipeline_id} {stage} output")
        summary_prompt = (
            f"Summarise the following pipeline stage output in 2-3 bullet points "
            f"for a Telegram message. Stage: {stage.upper()}. Be concise.\n\n{result[:4000]}"
        )
        try:
            summary, _ = await self._agent.run_turn(summary_prompt)
        except Exception as exc:
            log.warning("pipeline_summary_failed", stage=stage, error=str(exc))
            summary = result[:400]

        if gist_url:
            await self._notifier.send(f"{prefix}\n{summary}\n\nFull report: {gist_url}")
        else:
            await self._notifier.send(
                f"{prefix}\n{summary}\n\n(full output too long; gist creation failed)"
            )

    async def _run_llm_stage(
        self,
        pipeline_id: str,
        stage: str,
        task: str,
        context: str,
        artifacts: dict[str, str],
    ) -> str:
        """Run a team LLM stage (research/scope/plan/test/review).

        If the stage outputs CLARIFICATION_NEEDED: the pipeline pauses, asks the user
        via the notifier, and re-runs the stage with the answers in context (max 2 rounds).
        """
        team_id = _STAGE_TEAM[stage]
        team = self._teams.get_team(team_id)
        if team is None or not team["active"]:
            raise RuntimeError(f"Team '{team_id}' not found or inactive.")

        allowed = set(team["tools"]) - {"spawn_team", "spawn_agent", "run_pipeline"}
        subset = {n: t for n, t in self._registry.items() if n in allowed}

        extra_context = ""
        for round_ in range(_MAX_CLARIFICATION_ROUNDS + 1):
            prompt = _build_stage_prompt(stage, task, context + extra_context, artifacts)
            def _on_tokens(inp: int, out: int, _pid: str = pipeline_id) -> None:
                if self._job_registry is not None:
                    self._job_registry.add_tokens(_pid, inp, out)

            runner = SubAgentRunner(
                config=self._config,
                tools=subset,
                model=self._config.haiku_model,
                system_prefix=team["role"],
                label=f"{team_id}/{stage}",
                on_tokens=_on_tokens,
            )
            result, tokens = await runner.run(prompt)
            self._teams.log_task(
                team_id=team_id,
                task=prompt[:200],
                result=result,
                tokens_used=tokens,
                success=True,
                duration_s=0.0,
            )
            log.info(
                "pipeline_stage_done",
                pipeline_id=pipeline_id,
                stage=stage,
                tokens=tokens,
                clarification_round=round_,
            )

            if not result.startswith(_CLARIFICATION_PREFIX):
                return result  # happy path — got a real artifact

            if round_ == _MAX_CLARIFICATION_ROUNDS:
                raise RuntimeError(
                    f"Stage {stage} exceeded {_MAX_CLARIFICATION_ROUNDS} clarification rounds."
                )

            questions = result[len(_CLARIFICATION_PREFIX):].strip()
            await self._notifier.send(
                f"[Pipeline {pipeline_id} — {stage.upper()} needs clarification]\n\n{questions}"
            )
            answer = await self._notifier.ask_free_text(
                "Reply with your answers (5 min timeout):", timeout_s=300
            )
            if answer is None:
                raise RuntimeError(
                    f"Stage {stage} clarification timed out — no reply within 5 minutes."
                )
            extra_context += (
                f"\n\n## Clarification round {round_ + 1}\n"
                f"Questions:\n{questions}\n\nUser answers:\n{answer}"
            )

        raise RuntimeError("Unreachable")

    async def _run_implement(
        self,
        pipeline_id: str,
        task: str,
        workspace_path: str,
        language: str,
        artifacts: dict[str, str],
    ) -> str:
        """Run IMPLEMENT via Claude Code CLI directly in the workspace."""
        plan = artifacts.get(PipelineStage.PLAN, "")
        research = artifacts.get(PipelineStage.RESEARCH, "")
        scope = artifacts.get(PipelineStage.SCOPE, "")

        ccc_task = (
            f"## Task\n{task}\n\n"
            f"## Research findings\n{research[:1000]}\n\n"
            f"## Scope / requirements\n{scope[:800]}\n\n"
            f"## Implementation plan\n{plan}\n\n"
            f"Follow the plan precisely. TDD: write failing tests first, then implement. "
            f"Commit after each logical unit of work."
        )

        cmd = [_CLAUDE_BIN, *_CLAUDE_FLAGS, ccc_task]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=workspace_path,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to start Claude Code: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_CCC_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"IMPLEMENT stage timed out after {_CCC_TIMEOUT // 60} minutes.")

        if proc.returncode != 0:
            err = (stderr.decode(errors="replace") or stdout.decode(errors="replace"))[:800]
            raise RuntimeError(f"Claude Code exited {proc.returncode}: {err}")

        output = stdout.decode(errors="replace").strip()
        return output or "Implementation complete (no output captured)."

    async def _run_pr(
        self,
        pipeline_id: str,
        task: str,
        workspace_path: str,
        artifacts: dict[str, str],
    ) -> str:
        """Push branch and open PR."""
        branch = _branch_name(task, pipeline_id)
        review = artifacts.get(PipelineStage.REVIEW, "")
        impl = artifacts.get(PipelineStage.IMPLEMENT, "")

        # Create and push branch
        rc, _, err = await _run_git("git", "checkout", "-b", branch, cwd=workspace_path)
        if rc != 0:
            # Branch may already exist — try switching to it
            await _run_git("git", "checkout", branch, cwd=workspace_path)

        rc, _, err = await _run_git(
            "git", "push", "-u", "origin", branch, cwd=workspace_path
        )
        if rc != 0:
            raise RuntimeError(f"git push failed: {err}")

        body = (
            f"## Summary\n{impl[:600]}\n\n"
            f"## Review notes\n{review[:400]}\n\n"
            f"_Automated pipeline {pipeline_id}_"
        )
        rc, pr_url, err = await _run_git(
            "gh", "pr", "create",
            "--title", task[:72],
            "--body", body,
            "--head", branch,
            "--base", "main",
            cwd=workspace_path,
        )
        if rc != 0:
            raise RuntimeError(f"gh pr create failed: {err}")

        return pr_url or f"PR opened on branch {branch}."


# ------------------------------------------------------------------
# Prompt builders
# ------------------------------------------------------------------

def _build_stage_prompt(
    stage: str,
    task: str,
    context: str,
    artifacts: dict[str, str],
) -> str:
    parts = [f"## Task\n{task}"]
    if context:
        parts.append(f"## Context\n{context}")

    if stage == PipelineStage.RESEARCH:
        parts.append(
            "Research the best approach for implementing this task. "
            "Cover: relevant libraries, patterns, real-world examples, gotchas. "
            "End with a clear recommendation.\n\n"
            "If the task description is too vague to research meaningfully, output ONLY "
            "the line 'CLARIFICATION_NEEDED:' followed by a numbered list of your questions. "
            "Nothing else. Otherwise proceed with your research."
        )
    elif stage == PipelineStage.SCOPE:
        if PipelineStage.RESEARCH in artifacts:
            parts.append(f"## Research findings\n{artifacts[PipelineStage.RESEARCH][:1500]}")
        parts.append(
            "You are the architect. Define the complete scope for this project.\n\n"
            "If you genuinely need user input to proceed — e.g. what the app does, "
            "which external services to integrate, specific business rules you cannot infer — "
            "output ONLY the line 'CLARIFICATION_NEEDED:' followed by a numbered list of your questions. "
            "Nothing else in your response.\n\n"
            "For decisions that do not require user input (stack choice, folder structure, "
            "auth library, database schema, API shape): make the call yourself and document it.\n\n"
            "If you have enough context, produce the full scope artifact: "
            "what will be built, tech stack with rationale, acceptance criteria, out-of-scope items."
        )
    elif stage == PipelineStage.PLAN:
        if PipelineStage.RESEARCH in artifacts:
            parts.append(f"## Research\n{artifacts[PipelineStage.RESEARCH][:800]}")
        if PipelineStage.SCOPE in artifacts:
            parts.append(f"## Scope\n{artifacts[PipelineStage.SCOPE][:1000]}")
        parts.append(
            "Write a detailed implementation plan: files to create/modify, "
            "interfaces, data models, test strategy (TDD). Concrete enough to execute."
        )
    elif stage == PipelineStage.TEST:
        if PipelineStage.IMPLEMENT in artifacts:
            parts.append(f"## Implementation summary\n{artifacts[PipelineStage.IMPLEMENT][:800]}")
        parts.append(
            "Run the test suite, check coverage, identify gaps, add missing tests. "
            "Report: coverage %, failing tests with root cause, missing scenarios."
        )
    elif stage == PipelineStage.REVIEW:
        if PipelineStage.PLAN in artifacts:
            parts.append(f"## Original plan\n{artifacts[PipelineStage.PLAN][:600]}")
        if PipelineStage.IMPLEMENT in artifacts:
            parts.append(f"## Implementation\n{artifacts[PipelineStage.IMPLEMENT][:800]}")
        if PipelineStage.TEST in artifacts:
            parts.append(f"## Test results\n{artifacts[PipelineStage.TEST][:600]}")
        parts.append(
            "Review the implementation against the plan. Note deviations, quality issues, "
            "missing edge cases. Give a go/no-go recommendation for the PR."
        )

    return "\n\n".join(parts)
