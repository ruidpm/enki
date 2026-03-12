"""Save an artifact for the current pipeline stage.

Teams call this to record their output so the pipeline gate can advance.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.pipeline.store import PipelineStage, PipelineStore

log = structlog.get_logger()

_VALID_STAGES: frozenset[str] = frozenset(PipelineStage.ORDERED)


class SavePipelineArtifactTool:
    name = "save_pipeline_artifact"
    description = (
        "Save the output artifact for the current pipeline stage. "
        "Must be called before manage_pipeline advance can proceed. "
        "Use after completing a stage's work to record the result."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pipeline_id": {
                "type": "string",
                "description": "Pipeline ID",
            },
            "stage": {
                "type": "string",
                "enum": PipelineStage.ORDERED,
                "description": "Stage this artifact belongs to",
            },
            "artifact_type": {
                "type": "string",
                "description": (
                    "Type of artifact: research_report | requirements | "
                    "implementation_plan | implementation_summary | "
                    "test_results | review_summary | pr_url"
                ),
            },
            "content": {
                "type": "string",
                "description": "Full content of the artifact",
            },
        },
        "required": ["pipeline_id", "stage", "artifact_type", "content"],
    }

    def __init__(self, pipeline_store: PipelineStore) -> None:
        self._store = pipeline_store

    async def execute(self, **kwargs: Any) -> str:
        pipeline_id: str | None = kwargs.get("pipeline_id")
        stage: str | None = kwargs.get("stage")
        artifact_type: str | None = kwargs.get("artifact_type")
        content: str | None = kwargs.get("content")

        if not pipeline_id or not stage or not artifact_type or not content:
            missing = [
                f
                for f, v in [
                    ("pipeline_id", pipeline_id),
                    ("stage", stage),
                    ("artifact_type", artifact_type),
                    ("content", content),
                ]
                if not v
            ]
            return f"[ERROR] Required fields missing: {', '.join(missing)}"

        if stage not in _VALID_STAGES:
            return f"[ERROR] Invalid stage '{stage}'. Valid stages: {', '.join(PipelineStage.ORDERED)}"

        p = self._store.get(pipeline_id)
        if p is None:
            return f"[ERROR] Pipeline '{pipeline_id}' not found."

        self._store.save_artifact(pipeline_id, stage, artifact_type, content)
        log.info(
            "pipeline_artifact_saved",
            pipeline_id=pipeline_id,
            stage=stage,
            artifact_type=artifact_type,
            content_len=len(content),
        )
        return (
            f"Artifact saved for pipeline {pipeline_id}, stage {stage.upper()}. "
            f"Type: {artifact_type}. "
            f"You can now call manage_pipeline with action='advance' and pipeline_id='{pipeline_id}'."
        )
