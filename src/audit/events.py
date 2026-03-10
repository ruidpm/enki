"""Audit event type definitions — Tier 1 (security) and Tier 2 (activity)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Tier1Event(StrEnum):
    """Security events — stored forever, chain-hashed."""
    GUARDRAIL_BLOCK = "GUARDRAIL_BLOCK"
    COST_BLOCK = "COST_BLOCK"
    LOOP_DETECTED = "LOOP_DETECTED"
    EVOLUTION_PROPOSED = "EVOLUTION_PROPOSED"
    EVOLUTION_APPROVED = "EVOLUTION_APPROVED"
    EVOLUTION_REJECTED = "EVOLUTION_REJECTED"
    SESSION_START = "SESSION_START"
    SESSION_END = "SESSION_END"
    RESTART_REQUESTED = "RESTART_REQUESTED"
    RESTART_COMPLETED = "RESTART_COMPLETED"
    GIT_PUSH = "GIT_PUSH"
    PR_CREATED = "PR_CREATED"
    CLAUDE_CODE_SPAWN = "CLAUDE_CODE_SPAWN"


class Tier2Event(StrEnum):
    """Activity events — metadata only, 30-day rolling."""
    USER_MESSAGE = "USER_MESSAGE"
    LLM_CALL = "LLM_CALL"
    TOOL_CALLED = "TOOL_CALLED"
    CRON_TRIGGER = "CRON_TRIGGER"
    SUB_AGENT_SPAWN = "SUB_AGENT_SPAWN"


@dataclass
class AuditRecord:
    event_type: str
    session_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    data: dict[str, Any] = field(default_factory=dict)
    # Tier 1 only
    prev_chain_hash: str = ""
    chain_hash: str = ""
