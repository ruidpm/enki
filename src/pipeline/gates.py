"""Pipeline quality gates — deterministic + optional LLM evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog

log = structlog.get_logger()


class GateVerdict(StrEnum):
    PASS = "pass"
    RETRY = "retry"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class GateResult:
    verdict: GateVerdict
    reason: str
    retry_hint: str
    structural_ok: bool
    llm_score: float  # 0.0 if skipped


@dataclass(frozen=True)
class StageGate:
    required_keywords: list[str]  # at least one must appear (case-insensitive)
    min_length: int
    llm_judge_prompt: str | None  # None = no LLM eval
    pass_threshold: float
    max_retries: int


# Gate definitions per stage
STAGE_GATES: dict[str, StageGate] = {
    "research": StageGate(
        required_keywords=["recommend", "approach", "conclusion", "finding"],
        min_length=200,
        llm_judge_prompt=None,
        pass_threshold=0.0,
        max_retries=1,
    ),
    "scope": StageGate(
        required_keywords=["acceptance criteria"],
        min_length=300,
        llm_judge_prompt=None,
        pass_threshold=0.0,
        max_retries=1,
    ),
    "plan": StageGate(
        required_keywords=["test"],
        min_length=400,
        llm_judge_prompt=None,
        pass_threshold=0.0,
        max_retries=1,
    ),
    "implement": StageGate(
        required_keywords=[],
        min_length=50,
        llm_judge_prompt=None,
        pass_threshold=0.0,
        max_retries=0,
    ),
    "test": StageGate(
        required_keywords=[],  # handled in _check_structural special case
        min_length=100,
        llm_judge_prompt=None,
        pass_threshold=0.0,
        max_retries=1,
    ),
    "review": StageGate(
        required_keywords=["go", "no-go", "recommendation"],
        min_length=200,
        llm_judge_prompt=(
            "Does this code review check implementation against the plan, "
            "note quality issues, and give a clear go/no-go recommendation? "
            "Score 0.0-1.0. Reply with ONLY the number."
        ),
        pass_threshold=0.7,
        max_retries=1,
    ),
    "pr": StageGate(
        required_keywords=[],
        min_length=0,
        llm_judge_prompt=None,
        pass_threshold=0.0,
        max_retries=0,
    ),
}

# Regex for file path references in plan artifacts
_FILE_PATH_RE = re.compile(
    r"(?:src/|tests/|lib/|pkg/|cmd/|internal/|"
    r"[\w./]+\.(?:py|ts|go|rs|js|tsx|jsx|html|css|json|yaml|yml|toml|sh|sql|md|svelte|vue|rb|java|kt|swift|c|h|cpp))"
)


def _retry(reason: str) -> GateResult:
    """Build a RETRY result with the given reason as both reason and hint."""
    return GateResult(
        verdict=GateVerdict.RETRY,
        reason=reason,
        retry_hint=reason,
        structural_ok=False,
        llm_score=0.0,
    )


def _check_structural(stage: str, gate: StageGate, artifact: str) -> str | None:
    """Run structural checks. Return failure reason or None if OK."""
    lower = artifact.lower()

    # Length check
    if len(artifact) < gate.min_length:
        return f"Artifact too short: {len(artifact)} chars, need >= {gate.min_length}"

    # Stage-specific keyword / pattern checks
    if stage == "scope":
        has_ac = "acceptance criteria" in lower
        has_oos = "out of scope" in lower or "out-of-scope" in lower
        if not has_ac or not has_oos:
            missing: list[str] = []
            if not has_ac:
                missing.append("acceptance criteria")
            if not has_oos:
                missing.append("out of scope")
            return f"Missing required terms: {', '.join(missing)}"

    elif stage == "plan":
        has_test = "test" in lower
        file_refs = _FILE_PATH_RE.findall(artifact)
        if not has_test:
            return "Missing 'test' keyword in plan"
        if len(file_refs) < 3:
            return f"Need >= 3 file path references, found {len(file_refs)}"

    elif stage == "test":
        has_header = any(marker in lower for marker in ["test result", "test summary", "## test"])
        has_outcome = any(word in lower for word in ["passed", "failed", "✓", "✗"])
        if not has_header:
            return "Missing test report header (e.g. 'Test Results', 'Test Summary', '## Test')"
        if not has_outcome:
            return "Missing test outcome indicators (passed/failed/✓/✗)"

    elif stage == "pr":
        if not artifact.strip().startswith("http"):
            return "PR artifact must start with a URL (http...)"

    elif gate.required_keywords:
        # Generic: at least one keyword must appear
        if not any(kw in lower for kw in gate.required_keywords):
            return f"Missing required keyword (need one of: {', '.join(gate.required_keywords)})"

    return None


async def check_gate(
    stage: str,
    artifact: str,
    *,
    client: Any | None = None,
    model: str = "",
) -> GateResult:
    """Evaluate a stage artifact against its quality gate.

    Args:
        stage: Pipeline stage name (e.g. "research", "review").
        artifact: The text output from the stage.
        client: Optional anthropic.AsyncAnthropic for LLM eval.
        model: Model ID for LLM eval.

    Returns:
        GateResult with verdict, reason, and scores.
    """
    gate = STAGE_GATES.get(stage)
    if gate is None:
        return GateResult(
            verdict=GateVerdict.PASS,
            reason="unknown stage — auto-pass",
            retry_hint="",
            structural_ok=True,
            llm_score=0.0,
        )

    # Structural checks
    failure = _check_structural(stage, gate, artifact)
    if failure is not None:
        return _retry(failure)

    # LLM judge (only if configured and client provided)
    llm_score = 0.0
    if gate.llm_judge_prompt is not None and client is not None:
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=50,
                messages=[
                    {
                        "role": "user",
                        "content": (f"{gate.llm_judge_prompt}\n\n{artifact[:4000]}"),
                    }
                ],
            )
            raw = response.content[0].text
            match = re.search(r"(\d+\.?\d*)", raw)
            if match:
                llm_score = float(match.group(1))
                if llm_score < gate.pass_threshold:
                    return GateResult(
                        verdict=GateVerdict.RETRY,
                        reason=(f"LLM score {llm_score:.2f} below threshold {gate.pass_threshold}"),
                        retry_hint=(f"LLM score {llm_score:.2f} below threshold {gate.pass_threshold}"),
                        structural_ok=True,
                        llm_score=llm_score,
                    )
            else:
                log.warning(
                    "gate_llm_parse_failed",
                    stage=stage,
                    raw=raw,
                )
        except Exception:
            log.warning(
                "gate_llm_eval_error",
                stage=stage,
                exc_info=True,
            )

    return GateResult(
        verdict=GateVerdict.PASS,
        reason="all checks passed",
        retry_hint="",
        structural_ok=True,
        llm_score=llm_score,
    )
