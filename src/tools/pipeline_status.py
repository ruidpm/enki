"""Pipeline status query tool — read-only pipeline health for Enki."""

from __future__ import annotations

from typing import Any

from src.pipeline.store import PipelineStage, PipelineStore

_STAGE_ORDER: dict[str, int] = {s: i for i, s in enumerate(PipelineStage.ORDERED)}


class PipelineStatusTool:
    name = "pipeline_status"
    description = (
        "Get status of engineering pipelines. Without pipeline_id, lists all active pipelines. "
        "With pipeline_id, shows detailed stage progress, gate results, and artifact gist URLs."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pipeline_id": {
                "type": "string",
                "description": "Pipeline ID to query (omit to list all active)",
            },
        },
    }

    def __init__(self, pipeline_store: PipelineStore) -> None:
        self._pipelines = pipeline_store

    async def execute(self, **kwargs: Any) -> str:
        pipeline_id = kwargs.get("pipeline_id", "").strip()

        if not pipeline_id:
            return self._list_active()
        return self._get_detail(pipeline_id)

    def _list_active(self) -> str:
        pipelines = self._pipelines.list_active()
        if not pipelines:
            return "No active pipelines."
        lines = ["Active pipelines:\n"]
        for p in pipelines:
            lines.append(f"  {p['pipeline_id']}  {p['current_stage'].upper():12s}  {p['status']:10s}  {p['task'][:60]}")
        return "\n".join(lines)

    def _get_detail(self, pipeline_id: str) -> str:
        pipeline = self._pipelines.get(pipeline_id)
        if pipeline is None:
            return f"Pipeline '{pipeline_id}' not found."

        lines = [
            f"Pipeline {pipeline_id}",
            f"  Task: {pipeline['task'][:100]}",
            f"  Status: {pipeline['status']}",
            f"  Stage: {pipeline['current_stage'].upper()}",
            f"  Created: {pipeline['created_at']}",
            "",
            "Artifacts:",
        ]

        artifacts = self._pipelines.list_artifacts(pipeline_id)
        if not artifacts:
            lines.append("  (none yet)")
        else:
            # Sort by stage progression order
            artifacts.sort(key=lambda a: _STAGE_ORDER.get(a["stage"], 999))
            for a in artifacts:
                line = f"  {a['stage'].upper():12s}  {a['artifact_type']}"
                # These columns may not exist yet (migration pending)
                if a.get("gate_verdict"):
                    line += f"  gate={a['gate_verdict']}"
                if a.get("gate_score") is not None:
                    line += f"  score={a['gate_score']:.1f}"
                if a.get("gist_url"):
                    line += f"  {a['gist_url']}"
                lines.append(line)

        return "\n".join(lines)
