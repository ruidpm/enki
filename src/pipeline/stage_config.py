"""Per-stage configuration — single-shot vs agentic mode, model tier, step limits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class StageConfig:
    """Configuration for a single pipeline stage."""

    mode: Literal["single_shot", "agentic"]
    model_tier: str  # "opus" | "haiku" — resolved to actual model ID at runtime
    max_steps: int  # 1 for single_shot, per-stage for agentic
    tools_allowed: bool  # False strips all tools including CCC
    max_result_tokens: int = 4096  # max_tokens for API call


STAGE_CONFIGS: dict[str, StageConfig] = {
    "research": StageConfig(
        mode="agentic",
        model_tier="haiku",
        max_steps=10,
        tools_allowed=True,
    ),
    "scope": StageConfig(
        mode="single_shot",
        model_tier="opus",
        max_steps=1,
        tools_allowed=False,
        max_result_tokens=8192,
    ),
    "plan": StageConfig(
        mode="single_shot",
        model_tier="opus",
        max_steps=1,
        tools_allowed=False,
        max_result_tokens=8192,
    ),
    "test": StageConfig(
        mode="single_shot",
        model_tier="opus",
        max_steps=1,
        tools_allowed=False,
        max_result_tokens=8192,
    ),
    "review": StageConfig(
        mode="single_shot",
        model_tier="opus",
        max_steps=1,
        tools_allowed=False,
    ),
}
