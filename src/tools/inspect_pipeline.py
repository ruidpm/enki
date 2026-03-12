"""Pipeline inspection tool — per-stage cost/token/step breakdown, zero LLM calls."""

from __future__ import annotations

import json
from typing import Any

from src.pipeline.store import PipelineStore


class InspectPipelineTool:
    name = "inspect_pipeline"
    description = (
        "Inspect a pipeline's per-stage metrics: tokens, cost, steps, duration. "
        "Actions: 'summary' for overview table, 'steps' for detailed step log. "
        "No LLM call — instant SQL query."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pipeline_id": {
                "type": "string",
                "description": "The pipeline ID to inspect.",
            },
            "action": {
                "type": "string",
                "enum": ["summary", "steps"],
                "description": "'summary' for per-stage overview, 'steps' for detailed step log.",
            },
            "stage": {
                "type": "string",
                "description": "Optional: filter steps to a specific stage (only for action='steps').",
            },
        },
        "required": ["pipeline_id", "action"],
    }

    def __init__(self, pipeline_store: PipelineStore) -> None:
        self._store = pipeline_store

    async def execute(self, **kwargs: Any) -> str:
        pipeline_id: str = kwargs.get("pipeline_id", "")
        action: str = kwargs.get("action", "summary")
        stage: str | None = kwargs.get("stage")

        if not pipeline_id:
            return "[ERROR] pipeline_id is required."

        pipeline = self._store.get(pipeline_id)
        if pipeline is None:
            return f"[ERROR] Pipeline '{pipeline_id}' not found."

        if action == "summary":
            return self._format_summary(pipeline_id, pipeline)
        if action == "steps":
            return self._format_steps(pipeline_id, stage)
        return f"[ERROR] Unknown action '{action}'. Use 'summary' or 'steps'."

    def _format_summary(self, pipeline_id: str, pipeline: dict[str, Any]) -> str:
        from src.pipeline.store import PipelineStage

        lines = [
            f"## Pipeline {pipeline_id}",
            f"Task: {pipeline['task']}",
            f"Status: {pipeline['status']} | Stage: {pipeline['current_stage']}",
            f"Created: {pipeline['created_at']} | Updated: {pipeline['updated_at']}",
            "",
            "| Stage | Steps | In tokens | Out tokens | Cost USD | Duration |",
            "|-------|-------|-----------|------------|----------|----------|",
        ]

        total_cost = 0.0
        total_in = 0
        total_out = 0
        total_dur = 0

        for stage_name in PipelineStage.ORDERED:
            summary = self._store.get_stage_summary(pipeline_id, stage_name)
            steps = summary["total_steps"]
            if steps == 0:
                continue
            in_tok = summary["total_input_tokens"]
            out_tok = summary["total_output_tokens"]
            cost = summary["total_cost_usd"]
            dur_ms = summary["total_duration_ms"]
            total_cost += cost
            total_in += in_tok
            total_out += out_tok
            total_dur += dur_ms
            lines.append(
                f"| {stage_name:<8} | {steps:>5} | {in_tok:>9,} | {out_tok:>10,} | ${cost:>7.4f} | {dur_ms / 1000:.1f}s |"
            )

        lines.append(f"| **Total** | | {total_in:>9,} | {total_out:>10,} | ${total_cost:>7.4f} | {total_dur / 1000:.1f}s |")

        return "\n".join(lines)

    def _format_steps(self, pipeline_id: str, stage: str | None) -> str:
        steps = self._store.list_steps(pipeline_id, stage)
        if not steps:
            label = f" for stage '{stage}'" if stage else ""
            return f"No steps recorded{label} in pipeline '{pipeline_id}'."

        lines = [f"## Steps for pipeline {pipeline_id}" + (f" — stage: {stage}" if stage else "")]
        for s in steps:
            tools = json.loads(s["tools_called"]) if s["tools_called"] else []
            tool_names = [t["name"] for t in tools] if tools else ["(none)"]
            lines.append(
                f"Step {s['step_number']} [{s['stage']}] "
                f"— {s['input_tokens']}in/{s['output_tokens']}out "
                f"— ${s['cost_usd']:.4f} — {s['duration_ms']}ms "
                f"— tools: {', '.join(tool_names)}"
            )

        return "\n".join(lines)
