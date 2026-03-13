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
import json
import re
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from src.constants import REQUIRES_CONFIRM
from src.costs import model_cost_usd
from src.guardrails.cost_guard import CostGuardHook
from src.interfaces.notifier import Notifier
from src.output_delivery import OutputDelivery
from src.pipeline.gates import STAGE_GATES, GateResult, GateVerdict, check_gate
from src.pipeline.stage_config import STAGE_CONFIGS
from src.pipeline.store import PipelineStage, PipelineStatus, PipelineStore
from src.sub_agent import StepRecord, SubAgentRunner
from src.teams.store import TeamsStore
from src.tools.pipeline_ccc import PipelineCCCTool
from src.workspaces.store import WorkspaceStore

if TYPE_CHECKING:
    from src.jobs import JobRegistry

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
_MAX_CLARIFICATION_ROUNDS = 1

_PAUSE_POLL_INTERVAL = 5  # seconds between pause-status checks


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

    def __init__(
        self,
        notifier: Notifier,
        pipeline_store: PipelineStore,
        workspace_store: WorkspaceStore,
        teams_store: TeamsStore,
        config: Any,
        tool_registry: dict[str, Any],
        job_registry: JobRegistry | None = None,
        cost_guard: CostGuardHook | None = None,
        anthropic_client: Any = None,
        summary_model: str = "",
    ) -> None:
        self._notifier = notifier
        self._pipelines = pipeline_store
        self._workspaces = workspace_store
        self._teams = teams_store
        self._config = config
        self._registry = tool_registry
        self._job_registry: JobRegistry | None = job_registry
        self._cost_guard: CostGuardHook | None = cost_guard
        self._anthropic_client = anthropic_client
        self._summary_model = summary_model
        self._pipeline_ccc: PipelineCCCTool | None = None
        self._output = OutputDelivery(
            notifier=notifier,
            anthropic_client=anthropic_client,
            model=summary_model,
            job_registry=job_registry,
        )

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

        # Sync CCC instance for this pipeline run — injected into sub-agent tool subsets
        self._pipeline_ccc = PipelineCCCTool(
            workspace_path=workspace_path,
            language=language,
        )

        def _update_stage(stage: str) -> None:
            if self._job_registry is not None:
                self._job_registry.update_stage(pipeline_id, stage.upper())

        def _finish_job(success: bool, error: str | None = None) -> None:
            if self._job_registry is not None:
                self._job_registry.finish(pipeline_id, success=success, error=error)

        try:
            for stage in PipelineStage.ORDERED:
                # Check if pipeline was paused externally
                await self._wait_if_paused(pipeline_id, stage)

                _update_stage(stage)

                _auto_pass = GateResult(verdict=GateVerdict.PASS, reason="auto", retry_hint="", structural_ok=True, llm_score=0.0)

                if stage == PipelineStage.IMPLEMENT:
                    # Ensure we start from main, then create feature branch so CCC commits land on it
                    await _run_git("git", "checkout", "main", cwd=workspace_path)
                    branch = _branch_name(task, pipeline_id)
                    rc, _, _ = await _run_git("git", "checkout", "-b", branch, cwd=workspace_path)
                    if rc != 0:
                        await _run_git("git", "checkout", branch, cwd=workspace_path)
                    result = await self._run_implement(pipeline_id, task, workspace_path, language, artifacts)
                    gate_result = _auto_pass
                elif stage == PipelineStage.PR:
                    pr_confirmed = await self._notifier.ask_single_confirm(
                        reason=f"[Pipeline {pipeline_id}] Open pull request?",
                        changes_summary=(f"Task: {task[:200]}\nIMPLEMENT complete. Ready to push branch and open PR."),
                    )
                    if not pr_confirmed:
                        await self._notifier.send(
                            f"[Pipeline {pipeline_id}] PR skipped — code is on the workspace. Run create_pr manually when ready."
                        )
                        self._pipelines.set_status(pipeline_id, PipelineStatus.COMPLETED)
                        _finish_job(success=True)
                        return
                    result = await self._run_pr(pipeline_id, task, workspace_path, artifacts)
                    gate_result = _auto_pass
                else:
                    # Run deterministic browser check before TEST stage
                    if stage == PipelineStage.TEST:
                        from src.pipeline.browser_check import run_browser_check

                        browser_report = await run_browser_check(workspace_path)
                        if browser_report:
                            artifacts["_browser_check"] = browser_report

                    result, gate_result = await self._run_with_gate(pipeline_id, stage, task, context, artifacts)

                artifacts[stage] = result
                self._pipelines.save_artifact(pipeline_id, stage, _STAGE_ARTIFACT_TYPE[stage], result)

                # Create gist for every artifact
                gist_url = await self._output.create_gist(result, f"Pipeline {pipeline_id} — {stage.upper()}")
                self._pipelines.update_artifact_gate(
                    pipeline_id,
                    stage,
                    gate_verdict=gate_result.verdict,
                    gate_score=gate_result.llm_score if gate_result.llm_score > 0 else None,
                    gist_url=gist_url,
                )

                self._pipelines.advance_stage(
                    pipeline_id,
                    PipelineStage.next(stage) or stage,
                )

                # Notify with gate status
                gate_note = f" Gate: {gate_result.verdict.upper()}"
                if gate_result.llm_score > 0:
                    gate_note += f" (score: {gate_result.llm_score:.1f})"
                await self._send_stage_output(pipeline_id, stage, result, gate_note=gate_note, gist_url=gist_url)

                # SCOPE approval: always ask user to approve scope
                if stage == PipelineStage.SCOPE:
                    scope_approved = await self._scope_approval(pipeline_id, result)
                    if scope_approved is False:
                        self._pipelines.set_status(pipeline_id, PipelineStatus.ABORTED)
                        _finish_job(success=False, error="User rejected scope")
                        await self._notifier.send(f"[Pipeline {pipeline_id}] Aborted — scope not approved.")
                        return
                    if isinstance(scope_approved, str):
                        # User provided feedback — re-run scope with feedback
                        await self._notifier.send(f"[Pipeline {pipeline_id}] Got it — revising scope with your feedback.")
                        context += f"\n\n## User feedback on scope\n{scope_approved}"
                        result, _ = await self._run_with_gate(pipeline_id, stage, task, context, artifacts)
                        artifacts[stage] = result
                        self._pipelines.save_artifact(pipeline_id, stage, _STAGE_ARTIFACT_TYPE[stage], result)
                    elif scope_approved is True:
                        await self._notifier.send(f"[Pipeline {pipeline_id}] Scope approved — continuing to PLAN.")

        except asyncio.CancelledError:
            log.info("run_pipeline_cancelled", pipeline_id=pipeline_id)
            self._pipelines.set_status(pipeline_id, PipelineStatus.ABORTED)
            _finish_job(success=False, error="Cancelled")
            await self._notifier.send(f"[Pipeline {pipeline_id}] Cancelled.")
            raise
        except Exception as exc:
            log.error("run_pipeline_error", pipeline_id=pipeline_id, error=str(exc))
            self._pipelines.set_status(pipeline_id, PipelineStatus.ABORTED)
            _finish_job(success=False, error=str(exc))
            await self._notifier.send(f"[Pipeline {pipeline_id}] ERROR — pipeline aborted.\n{exc}")
            return

        self._pipelines.set_status(pipeline_id, PipelineStatus.COMPLETED)
        _finish_job(success=True)

        # Combined multi-file gist with all artifacts
        combined_gist = await self._output.create_multi_file_gist(
            {f"{s}.md": content for s, content in artifacts.items()},
            f"Pipeline {pipeline_id} — all artifacts",
        )

        pr_url = artifacts.get(PipelineStage.PR, "PR creation failed — check logs.")
        gist_note = f"\nAll artifacts: {combined_gist}" if combined_gist else ""
        await self._notifier.send(
            f"[Pipeline {pipeline_id}] DONE. All stages complete.\nPR: {pr_url}{gist_note}\nReview and merge when ready."
        )

    async def _send_stage_output(
        self,
        pipeline_id: str,
        stage: str,
        result: str,
        *,
        gate_note: str = "",
        gist_url: str | None = None,
    ) -> None:
        """Send stage output to notifier. Title + gist link only (full report in gist)."""
        title = f"[Pipeline {pipeline_id}] {stage.upper()} complete.{gate_note}"
        if gist_url:
            await self._notifier.send(f"{title}\nFull report: {gist_url}")
        else:
            # No gist — fall back to truncated output
            await self._notifier.send(f"{title}\n{result[:800]}")

    async def _wait_if_paused(self, pipeline_id: str, stage: str) -> None:
        """Block until pipeline status is no longer PAUSED."""
        p = self._pipelines.get(pipeline_id)
        if p and p["status"] == PipelineStatus.PAUSED:
            await self._notifier.send(
                f"[Pipeline {pipeline_id}] Paused before {stage.upper()}. Use manage_pipeline resume to continue."
            )
            while True:
                await asyncio.sleep(_PAUSE_POLL_INTERVAL)
                p = self._pipelines.get(pipeline_id)
                if not p or p["status"] != PipelineStatus.PAUSED:
                    break
            if p and p["status"] == PipelineStatus.ABORTED:
                raise asyncio.CancelledError()

    async def _scope_approval(self, pipeline_id: str, scope_artifact: str) -> bool | str:
        """Ask user to approve scope via 3-button keyboard. Returns True, False, or feedback string."""
        summary = await self._output._summarize(scope_artifact, " This is a project scope document.")
        display = summary or scope_artifact[:600]
        answer = await self._notifier.ask_scope_approval(
            f"[Pipeline {pipeline_id}] SCOPE complete — please review:\n\n{display}",
            timeout_s=600,
        )
        if answer is None:
            # Timeout — pause instead of abort
            self._pipelines.set_status(pipeline_id, PipelineStatus.PAUSED)
            await self._notifier.send(
                f"[Pipeline {pipeline_id}] Scope approval timed out — pipeline paused. "
                f"Use manage_pipeline resume after reviewing."
            )
            # Wait for resume
            await self._wait_if_paused(pipeline_id, PipelineStage.PLAN)
            return True  # resumed means approved
        if answer == "approve":
            return True
        if answer == "reject":
            return False
        return answer  # feedback string from Revise flow

    async def _run_with_gate(
        self,
        pipeline_id: str,
        stage: str,
        task: str,
        context: str,
        artifacts: dict[str, str],
    ) -> tuple[str, GateResult]:
        """Run an LLM stage with quality gate check and retry on failure.

        Returns (result_text, gate_result) so callers can record the verdict
        without re-checking the gate.
        """
        _pass = GateResult(verdict=GateVerdict.PASS, reason="no gate", retry_hint="", structural_ok=True, llm_score=0.0)

        result = await self._run_llm_stage(pipeline_id, stage, task, context, artifacts)

        gate = STAGE_GATES.get(stage)
        if gate is None or gate.max_retries == 0:
            return result, _pass

        gate_result = await check_gate(
            stage,
            result,
            client=self._anthropic_client,
            model=self._summary_model,
        )

        if gate_result.verdict == GateVerdict.PASS:
            return result, gate_result

        # Retry once with gate feedback
        log.info("pipeline_gate_retry", pipeline_id=pipeline_id, stage=stage, reason=gate_result.reason)
        retry_context = (
            f"\n\n## Quality gate feedback (retry)\n"
            f"Your previous output was rejected: {gate_result.reason}\n"
            f"Please address: {gate_result.retry_hint}"
        )
        result = await self._run_llm_stage(pipeline_id, stage, task, context + retry_context, artifacts)

        gate_result = await check_gate(
            stage,
            result,
            client=self._anthropic_client,
            model=self._summary_model,
        )

        if gate_result.verdict == GateVerdict.PASS:
            return result, gate_result

        # Retry exhausted — escalate to user
        gist_url = await self._output.create_gist(result, f"Pipeline {pipeline_id} — {stage.upper()} (gate failed)")
        gist_note = f"\nArtifact: {gist_url}" if gist_url else ""
        answer = await self._notifier.ask_free_text(
            f"[Pipeline {pipeline_id}] {stage.upper()} gate failed after retry.\n"
            f"Reason: {gate_result.reason}{gist_note}\n\n"
            f"Reply 'skip' to accept as-is, 'abort' to stop, or provide guidance for another attempt.",
            timeout_s=600,
        )

        if answer is None or answer.strip().lower() == "abort":
            raise RuntimeError(f"Stage {stage} gate failed — user aborted.")
        if answer.strip().lower() == "skip":
            return result, gate_result
        # User provided guidance — one more try
        guidance_context = f"\n\n## User guidance\n{answer}"
        final_result = await self._run_llm_stage(pipeline_id, stage, task, context + guidance_context, artifacts)
        return final_result, _pass

    def _resolve_model(self, tier: str) -> str:
        """Resolve model tier string to actual model ID from config."""
        if tier == "opus":
            return str(self._config.opus_model)
        return str(self._config.haiku_model)

    def _make_on_step(self, pipeline_id: str, stage: str) -> Callable[[StepRecord], None]:
        """Create an on_step callback that persists step data to the pipeline store."""

        def _on_step(record: StepRecord) -> None:
            tool_data = [{"name": t.name, "input": t.input_preview, "output": t.output_preview} for t in record.tools]
            self._pipelines.save_step(
                pipeline_id,
                stage,
                record.step_number,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cost_usd=record.cost_usd,
                tools_called_json=json.dumps(tool_data),
                duration_ms=record.duration_ms,
            )
            log.info(
                "pipeline_step_audit",
                pipeline_id=pipeline_id,
                stage=stage,
                step=record.step_number,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cost_usd=round(record.cost_usd, 6),
                tools=[t.name for t in record.tools],
                duration_ms=record.duration_ms,
            )

        return _on_step

    async def _run_single_shot(
        self,
        pipeline_id: str,
        stage: str,
        task: str,
        context: str,
        artifacts: dict[str, str],
        stage_config: Any,
    ) -> tuple[str, int]:
        """Run a single-shot LLM stage — one API call, no tools, no loop."""
        team_id = _STAGE_TEAM[stage]
        team = self._teams.get_team(team_id)
        role = team["role"] if team else ""

        prompt = _build_stage_prompt(stage, task, context, artifacts)
        model = self._resolve_model(stage_config.model_tier)

        _base = (
            "You are a helpful sub-agent. Complete the given task and return a concise result. "
            "Plain text only — no markdown, no **bold**, no # headers, no bullet points with *, "
            "no backtick code blocks."
        )
        system = ((role + "\n\n") if role else "") + _base

        step_start = time.monotonic()
        response = await self._anthropic_client.messages.create(
            model=model,
            max_tokens=stage_config.max_result_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        step_in = response.usage.input_tokens
        step_out = response.usage.output_tokens
        total_tokens = step_in + step_out
        cost = model_cost_usd(model, step_in, step_out)
        duration_ms = int((time.monotonic() - step_start) * 1000)

        # Record tokens/cost
        if self._job_registry is not None:
            self._job_registry.add_tokens(pipeline_id, step_in, step_out)
        if self._cost_guard is not None:
            self._cost_guard.record_llm_call(step_in, step_out, cost)

        # Record step audit
        on_step = self._make_on_step(pipeline_id, stage)
        on_step(
            StepRecord(
                step_number=1,
                input_tokens=step_in,
                output_tokens=step_out,
                cost_usd=cost,
                tools=[],
                duration_ms=duration_ms,
            )
        )

        # Extract text
        from anthropic.types import TextBlock as _TextBlock

        texts = [b.text for b in response.content if isinstance(b, _TextBlock)]
        result = "\n".join(texts) if texts else "(no response)"

        # Log team task
        self._teams.log_task(
            team_id=team_id,
            task=prompt[:200],
            result=result,
            tokens_used=total_tokens,
            success=True,
            duration_s=duration_ms / 1000.0,
        )

        log.info(
            "pipeline_stage_summary",
            pipeline_id=pipeline_id,
            stage=stage,
            mode="single_shot",
            model=model,
            total_steps=1,
            total_tokens=total_tokens,
            total_cost_usd=round(cost, 6),
            duration_s=round(duration_ms / 1000.0, 2),
        )

        return result, total_tokens

    async def _run_llm_stage(
        self,
        pipeline_id: str,
        stage: str,
        task: str,
        context: str,
        artifacts: dict[str, str],
    ) -> str:
        """Run a team LLM stage (research/scope/plan/test/review).

        Single-shot stages (scope/plan/review) make one direct API call.
        Agentic stages (research/test) use SubAgentRunner with tool access.

        If the stage outputs CLARIFICATION_NEEDED: the pipeline pauses, asks the user
        via the notifier, and re-runs the stage with the answers in context (max 2 rounds).
        """
        stage_config = STAGE_CONFIGS.get(stage)

        # Single-shot path: one API call, no tools, no loop
        if stage_config is not None and stage_config.mode == "single_shot":
            result, _tokens = await self._run_single_shot(
                pipeline_id,
                stage,
                task,
                context,
                artifacts,
                stage_config,
            )
            return result

        # Agentic path: SubAgentRunner with tools
        team_id = _STAGE_TEAM[stage]
        team = self._teams.get_team(team_id)
        if team is None or not team["active"]:
            raise RuntimeError(f"Team '{team_id}' not found or inactive.")

        allowed = set(team["tools"]) - {"spawn_team", "spawn_agent", "run_pipeline", "run_claude_code"} - REQUIRES_CONFIRM
        subset = {n: t for n, t in self._registry.items() if n in allowed}
        # Inject sync CCC only for agentic stages that allow tools
        if self._pipeline_ccc is not None and (stage_config is None or stage_config.tools_allowed):
            subset["run_code_task"] = self._pipeline_ccc

        max_steps = stage_config.max_steps if stage_config else 80
        model = self._resolve_model(stage_config.model_tier) if stage_config else self._config.haiku_model
        on_step_cb = self._make_on_step(pipeline_id, stage)

        extra_context = ""
        stage_start = time.monotonic()
        stage_tokens = 0

        for round_ in range(_MAX_CLARIFICATION_ROUNDS + 1):
            prompt = _build_stage_prompt(stage, task, context + extra_context, artifacts)

            def _on_tokens(inp: int, out: int, _pid: str = pipeline_id) -> None:
                if self._job_registry is not None:
                    self._job_registry.add_tokens(_pid, inp, out)

            def _on_cost(inp: int, out: int, cost: float) -> None:
                if self._cost_guard is not None:
                    self._cost_guard.record_llm_call(inp, out, cost)

            def _cancel_check(_pid: str = pipeline_id) -> bool:
                p = self._pipelines.get(_pid)
                return p is not None and p["status"] in (PipelineStatus.PAUSED, PipelineStatus.ABORTED)

            runner = SubAgentRunner(
                config=self._config,
                tools=subset,
                model=model,
                max_steps=max_steps,
                system_prefix=team["role"],
                label=f"{team_id}/{stage}",
                on_tokens=_on_tokens,
                on_cost=_on_cost,
                on_step=on_step_cb,
                cancel_check=_cancel_check,
            )
            result, tokens = await runner.run(prompt)
            stage_tokens += tokens

            # Handle cancellation during stage
            if result.startswith("[CANCELLED]"):
                raise asyncio.CancelledError(f"Stage {stage} cancelled: pipeline paused or aborted.")

            self._teams.log_task(
                team_id=team_id,
                task=prompt[:200],
                result=result,
                tokens_used=tokens,
                success=True,
                duration_s=0.0,
            )

            log.info(
                "pipeline_stage_summary",
                pipeline_id=pipeline_id,
                stage=stage,
                mode="agentic",
                model=model,
                total_tokens=stage_tokens,
                duration_s=round(time.monotonic() - stage_start, 2),
                clarification_round=round_,
            )

            if not result.startswith(_CLARIFICATION_PREFIX):
                return result  # happy path — got a real artifact

            if round_ == _MAX_CLARIFICATION_ROUNDS:
                raise RuntimeError(f"Stage {stage} exceeded {_MAX_CLARIFICATION_ROUNDS} clarification rounds.")

            questions = result[len(_CLARIFICATION_PREFIX) :].strip()
            await self._notifier.send(f"[Pipeline {pipeline_id} — {stage.upper()} needs clarification]\n\n{questions}")
            answer = await self._notifier.ask_free_text("Reply with your answers (5 min timeout):", timeout_s=300)
            if answer is None:
                raise RuntimeError(f"Stage {stage} clarification timed out — no reply within 5 minutes.")
            extra_context += f"\n\n## Clarification round {round_ + 1}\nQuestions:\n{questions}\n\nUser answers:\n{answer}"

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
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_CCC_TIMEOUT)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"IMPLEMENT stage timed out after {_CCC_TIMEOUT // 60} minutes.") from exc

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

        # Branch was created before IMPLEMENT — ensure we're on it and it has commits
        await _run_git("git", "checkout", branch, cwd=workspace_path)

        # Verify branch has commits ahead of main (CCC may have failed to commit)
        _, ahead, _ = await _run_git("git", "rev-list", "--count", "main..HEAD", cwd=workspace_path)
        if ahead.strip() == "0":
            raise RuntimeError("No commits on feature branch — IMPLEMENT may have failed to produce changes.")

        rc, _, err = await _run_git("git", "push", "-u", "origin", branch, cwd=workspace_path)
        if rc != 0:
            raise RuntimeError(f"git push failed: {err}")

        body = f"## Summary\n{impl[:600]}\n\n## Review notes\n{review[:400]}\n\n_Automated pipeline {pipeline_id}_"
        rc, pr_url, err = await _run_git(
            "gh",
            "pr",
            "create",
            "--title",
            task[:72],
            "--body",
            body,
            "--head",
            branch,
            "--base",
            "main",
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
    """Build the prompt for a pipeline stage.

    Single-shot stages (SCOPE/PLAN/REVIEW) get full prior artifacts — no truncation.
    Agentic stages (RESEARCH/TEST) get truncated artifacts to limit context growth.
    """
    is_single_shot = STAGE_CONFIGS.get(stage, None) is not None and STAGE_CONFIGS[stage].mode == "single_shot"

    parts = [f"## Task\n{task}"]
    if context:
        parts.append(f"## Context\n{context}")

    if stage == PipelineStage.RESEARCH:
        parts.append(
            "Research the best approach for implementing this task. "
            "Use your tools to search for libraries, patterns, real-world examples, and gotchas.\n\n"
            "If the task description is too vague to research meaningfully, output ONLY "
            "the line 'CLARIFICATION_NEEDED:' followed by a numbered list of your questions. "
            "Nothing else. Otherwise proceed with your research.\n\n"
            "Structure your output with these exact sections:\n"
            "## Key Findings\n"
            "## Recommended Approach\n"
            "## Libraries and Dependencies\n"
            "## Risks and Gotchas"
        )
    elif stage == PipelineStage.SCOPE:
        if PipelineStage.RESEARCH in artifacts:
            _art = artifacts[PipelineStage.RESEARCH] if is_single_shot else artifacts[PipelineStage.RESEARCH][:1500]
            parts.append(f"## Research findings\n{_art}")
        parts.append(
            "You are the architect. You have ONE response to produce the complete scope.\n"
            "Do NOT ask for tools or try to explore — use the research findings above.\n\n"
            "For decisions that do not require user input (stack choice, folder structure, "
            "auth library, database schema, API shape): make the call yourself and document it.\n\n"
            "Structure your output with these exact sections:\n"
            "## What Will Be Built\n"
            "## Tech Stack and Rationale\n"
            "## Acceptance Criteria\n"
            "## Out of Scope"
        )
    elif stage == PipelineStage.PLAN:
        if PipelineStage.RESEARCH in artifacts:
            _art = artifacts[PipelineStage.RESEARCH] if is_single_shot else artifacts[PipelineStage.RESEARCH][:800]
            parts.append(f"## Research\n{_art}")
        if PipelineStage.SCOPE in artifacts:
            _art = artifacts[PipelineStage.SCOPE] if is_single_shot else artifacts[PipelineStage.SCOPE][:1000]
            parts.append(f"## Scope\n{_art}")
        parts.append(
            "Write the implementation plan in ONE response. Use only the research and scope above.\n\n"
            "Structure your output with these exact sections:\n"
            "## Files to Create/Modify (with full paths)\n"
            "## Interfaces and Data Models\n"
            "## Implementation Steps (ordered)\n"
            "## Test Strategy (TDD)"
        )
    elif stage == PipelineStage.TEST:
        if PipelineStage.PLAN in artifacts:
            _art = artifacts[PipelineStage.PLAN] if is_single_shot else artifacts[PipelineStage.PLAN][:800]
            parts.append(f"## Plan\n{_art}")
        if PipelineStage.IMPLEMENT in artifacts:
            _art = artifacts[PipelineStage.IMPLEMENT] if is_single_shot else artifacts[PipelineStage.IMPLEMENT][:800]
            parts.append(f"## Implementation summary\n{_art}")
        if "_browser_check" in artifacts:
            parts.append(artifacts["_browser_check"])
        parts.append(
            "You are QA. Review the implementation against the plan and produce a quality report.\n"
            "You have NO tools — do NOT attempt to run, create, or modify any code.\n"
            "Analyze the implementation for:\n"
            "- Deviations from the plan\n"
            "- Missing test paths or untested edge cases\n"
            "- Security concerns (input validation, auth, data exposure)\n"
            "- Code quality issues visible from the implementation summary\n\n"
            "Structure your output with these exact sections:\n"
            "## Test Summary\n"
            "(Overall assessment with brief rationale)\n"
            "## Deviations from Plan\n"
            "## Missing Test Paths\n"
            "## Edge Cases and Security\n"
            "## Verdict\n"
            "(One of: passed — ready for review / failed — needs rework, with specific items)"
        )
    elif stage == PipelineStage.REVIEW:
        if PipelineStage.PLAN in artifacts:
            _art = artifacts[PipelineStage.PLAN] if is_single_shot else artifacts[PipelineStage.PLAN][:600]
            parts.append(f"## Original plan\n{_art}")
        if PipelineStage.IMPLEMENT in artifacts:
            _art = artifacts[PipelineStage.IMPLEMENT] if is_single_shot else artifacts[PipelineStage.IMPLEMENT][:800]
            parts.append(f"## Implementation\n{_art}")
        if PipelineStage.TEST in artifacts:
            _art = artifacts[PipelineStage.TEST] if is_single_shot else artifacts[PipelineStage.TEST][:600]
            parts.append(f"## Test results\n{_art}")
        parts.append(
            "Review in ONE response. No tools needed — use the artifacts above.\n\n"
            "Structure your output with these exact sections:\n"
            "## Deviations from Plan\n"
            "## Quality Issues\n"
            "## Missing Edge Cases\n"
            "## Go/No-Go Recommendation"
        )

    return "\n\n".join(parts)
