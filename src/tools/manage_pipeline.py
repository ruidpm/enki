"""Pipeline management tool.

ManagePipelineTool — start / advance / abort / list / status.

A pipeline runs through these stages in order, with a user gate between each:
  RESEARCH → SCOPE → PLAN → IMPLEMENT → TEST → REVIEW → PR

Each stage must produce an artifact before the user can advance to the next.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog

from src.pipeline.store import PipelineStage, PipelineStatus, PipelineStore
from src.workspaces.store import WorkspaceStore

if TYPE_CHECKING:
    from src.jobs import JobRegistry

log = structlog.get_logger()

_STAGE_TEAM: dict[str, str] = {
    PipelineStage.RESEARCH: "researcher",
    PipelineStage.SCOPE: "architect",
    PipelineStage.PLAN: "architect",
    PipelineStage.IMPLEMENT: "backend-dev",
    PipelineStage.TEST: "qa",
    PipelineStage.REVIEW: "architect",
    PipelineStage.PR: "devops",
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


class ManagePipelineTool:
    name = "manage_pipeline"
    description = (
        "Manage structured engineering pipelines on external workspaces. "
        "Actions: start | advance | abort | list | status. "
        "A pipeline runs: RESEARCH → SCOPE → PLAN → IMPLEMENT → TEST → REVIEW → PR. "
        "Each stage must produce an artifact before advancing. "
        "Call 'advance' after reviewing the current stage's artifact to move forward."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "advance", "abort", "list", "status"],
            },
            "workspace_id": {"type": "string"},
            "pipeline_id": {"type": "string"},
            "task": {"type": "string", "description": "What to build (for 'start')"},
            "feedback": {
                "type": "string",
                "description": "Optional feedback injected into the next stage's prompt",
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        pipeline_store: PipelineStore,
        workspace_store: WorkspaceStore,
        job_registry: JobRegistry | None = None,
    ) -> None:
        self._pipelines = pipeline_store
        self._workspaces = workspace_store
        self._job_registry = job_registry

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs.get("action", "")
        dispatch = {
            "start": self._start,
            "advance": self._advance,
            "abort": self._abort,
            "list": self._list,
            "status": self._status,
        }
        handler = dispatch.get(action)
        if handler is None:
            return f"[ERROR] Unknown action '{action}'. Valid: start | advance | abort | list | status"
        return await handler(**kwargs)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _start(self, **kwargs: Any) -> str:
        workspace_id: str = kwargs.get("workspace_id", "").strip()
        task: str = kwargs.get("task", "").strip()

        if not workspace_id:
            return "[ERROR] workspace_id is required."
        if not task:
            return "[ERROR] task is required."

        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return f"[ERROR] Workspace '{workspace_id}' not found. Use list_workspaces."

        pipeline_id = str(uuid.uuid4())[:8]
        self._pipelines.create(pipeline_id, workspace_id=workspace_id, task=task)

        team = _STAGE_TEAM[PipelineStage.RESEARCH]
        log.info(
            "pipeline_started",
            pipeline_id=pipeline_id,
            workspace_id=workspace_id,
            task=task[:100],
        )
        return (
            f"Pipeline {pipeline_id} started for workspace '{workspace_id}'.\n"
            f"Task: {task}\n"
            f"Current stage: RESEARCH (team: {team})\n\n"
            f"Next: delegate the research task to the '{team}' team, then call "
            f"manage_pipeline with action='advance' and pipeline_id='{pipeline_id}' "
            f"once you have results to save."
        )

    async def _advance(self, **kwargs: Any) -> str:
        pipeline_id: str = kwargs.get("pipeline_id", "").strip()
        feedback: str = kwargs.get("feedback", "")

        if not pipeline_id:
            return "[ERROR] pipeline_id is required."

        p = self._pipelines.get(pipeline_id)
        if p is None:
            return f"[ERROR] Pipeline '{pipeline_id}' not found."
        if p["status"] != PipelineStatus.ACTIVE:
            return f"[ERROR] Pipeline '{pipeline_id}' is {p['status']} — cannot advance."

        current = p["current_stage"]

        # Require artifact from current stage before advancing
        artifact = self._pipelines.get_artifact(pipeline_id, current)
        if artifact is None:
            team = _STAGE_TEAM.get(current, "?")
            artifact_type = _STAGE_ARTIFACT_TYPE.get(current, "artifact")
            return (
                f"[ERROR] Cannot advance — no artifact for stage '{current}'.\n"
                f"Complete the stage first: delegate to '{team}' team, then save the "
                f"{artifact_type} with save_pipeline_artifact (or have the team do it)."
            )

        next_stage = PipelineStage.next(current)

        if next_stage is None:
            # Already at PR — mark completed
            self._pipelines.set_status(pipeline_id, PipelineStatus.COMPLETED)
            log.info("pipeline_completed", pipeline_id=pipeline_id)
            pr_artifact = self._pipelines.get_artifact(pipeline_id, PipelineStage.PR)
            pr_url = pr_artifact["content"] if pr_artifact else "unknown"
            return f"Pipeline {pipeline_id} completed.\nPR: {pr_url}"

        self._pipelines.advance_stage(pipeline_id, next_stage)
        team = _STAGE_TEAM.get(next_stage, "?")
        artifact_type = _STAGE_ARTIFACT_TYPE.get(next_stage, "artifact")

        feedback_note = f"\nUser feedback: {feedback}" if feedback else ""
        log.info("pipeline_advanced", pipeline_id=pipeline_id, from_stage=current, to_stage=next_stage)
        return (
            f"Pipeline {pipeline_id} advanced: {current.upper()} → {next_stage.upper()}\n"
            f"Next team: '{team}'\n"
            f"Expected output: {artifact_type}{feedback_note}\n\n"
            f"Delegate the {next_stage} task to the '{team}' team with context from "
            f"previous artifacts, then advance again when done."
        )

    async def _abort(self, **kwargs: Any) -> str:
        pipeline_id: str = kwargs.get("pipeline_id", "").strip()
        if not pipeline_id:
            return "[ERROR] pipeline_id is required."

        p = self._pipelines.get(pipeline_id)
        if p is None:
            return f"[ERROR] Pipeline '{pipeline_id}' not found."

        self._pipelines.set_status(pipeline_id, PipelineStatus.ABORTED)
        killed = False
        if self._job_registry is not None:
            killed = self._job_registry.cancel(pipeline_id)
        log.info("pipeline_aborted", pipeline_id=pipeline_id, task_cancelled=killed)
        return f"Pipeline {pipeline_id} aborted." + (" Background task cancelled." if killed else "")

    async def _list(self, **kwargs: Any) -> str:
        pipelines = self._pipelines.list_active()
        if not pipelines:
            return "No active pipelines."

        lines = [f"Active pipelines ({len(pipelines)}):\n"]
        for p in pipelines:
            ws = self._workspaces.get(p["workspace_id"])
            ws_name = ws["name"] if ws else p["workspace_id"]
            lines.append(
                f"  {p['pipeline_id']}  [{p['current_stage'].upper()}]  {ws_name}\n"
                f"    Task: {p['task'][:80]}\n"
                f"    Started: {p['created_at']}\n"
            )
        return "\n".join(lines)

    async def _status(self, **kwargs: Any) -> str:
        pipeline_id: str = kwargs.get("pipeline_id", "").strip()
        if not pipeline_id:
            return "[ERROR] pipeline_id is required."

        p = self._pipelines.get(pipeline_id)
        if p is None:
            return f"[ERROR] Pipeline '{pipeline_id}' not found."

        artifacts = self._pipelines.list_artifacts(pipeline_id)
        completed_stages = {a["stage"] for a in artifacts}

        lines = [
            f"Pipeline: {pipeline_id}",
            f"Workspace: {p['workspace_id']}",
            f"Task: {p['task']}",
            f"Status: {p['status']}",
            f"Current stage: {p['current_stage'].upper()}",
            "",
            "Stages:",
        ]
        for stage in PipelineStage.ORDERED:
            done = "✓" if stage in completed_stages else ("→" if stage == p["current_stage"] else " ")
            lines.append(f"  {done} {stage.upper()}")

        if artifacts:
            lines.append("\nArtifacts:")
            for a in artifacts:
                preview = a["content"][:120].replace("\n", " ")
                lines.append(f"  [{a['stage'].upper()}] {a['artifact_type']}: {preview}...")

        return "\n".join(lines)
