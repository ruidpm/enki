"""Automated audit trail verification — detects integrity violations and anomalies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog

from .db import AuditDB
from .query import AuditQuery

log = structlog.get_logger()

_BLOCK_THRESHOLD = 10  # >N guardrail blocks in 24h = anomaly


@dataclass
class VerificationResult:
    """Result of an automated audit verification run."""

    chain_valid: bool
    chain_message: str
    anomalies: list[str] = field(default_factory=list)
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.summary:
            self.summary = self._build_summary()

    def _build_summary(self) -> str:
        status = "HEALTHY" if self.chain_valid and not self.anomalies else "ISSUES DETECTED"
        parts = [f"Audit verification: {status}"]
        parts.append(f"Chain integrity: {'OK' if self.chain_valid else 'BROKEN — ' + self.chain_message}")
        if self.anomalies:
            parts.append(f"Anomalies ({len(self.anomalies)}):")
            for a in self.anomalies:
                parts.append(f"  - {a}")
        else:
            parts.append("No anomalies detected.")
        return "\n".join(parts)


class AuditVerifier:
    """Runs automated checks on the audit trail."""

    def __init__(self, db: AuditDB) -> None:
        self._db = db
        self._query = AuditQuery(db)

    async def run_verification(self) -> VerificationResult:
        """Run all verification checks and return a structured result."""
        # 1. Verify hash chain integrity
        chain_valid, chain_message = self._query.verify_chain()

        # 2. Check for anomalies in the last 24 hours
        anomalies = self._check_anomalies()

        result = VerificationResult(
            chain_valid=chain_valid,
            chain_message=chain_message,
            anomalies=anomalies,
        )

        log.info(
            "audit_verification_complete",
            chain_valid=chain_valid,
            anomaly_count=len(anomalies),
        )

        return result

    def _check_anomalies(self) -> list[str]:
        """Check for anomalous patterns in the last 24 hours."""
        anomalies: list[str] = []
        since = datetime.now(tz=UTC) - timedelta(hours=24)

        # Check guardrail block rate
        events = self._query.get_security_events(since=since)
        block_count = len(events)
        if block_count > _BLOCK_THRESHOLD:
            anomalies.append(
                f"High guardrail block rate: {block_count} blocks in last 24h (threshold: {_BLOCK_THRESHOLD})"
            )

        # Check for repeated blocks on the same tool
        tool_blocks: dict[str, int] = {}
        for event in events:
            data = event.get("data", {})
            tool = data.get("tool", "unknown")
            tool_blocks[tool] = tool_blocks.get(tool, 0) + 1

        for tool, count in tool_blocks.items():
            if count >= 5:
                anomalies.append(
                    f"Repeated blocks on tool '{tool}': {count} times in 24h"
                )

        return anomalies
