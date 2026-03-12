"""Tests for automated audit trail verification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.audit.verifier import AuditVerifier, VerificationResult


class TestAuditVerifier:
    @pytest.fixture
    def audit_db(self, tmp_path: Path) -> MagicMock:
        from src.audit.db import AuditDB

        return AuditDB(tmp_path / "audit.db")

    @pytest.fixture
    def verifier(self, audit_db: MagicMock) -> AuditVerifier:
        return AuditVerifier(audit_db)

    @pytest.mark.asyncio
    async def test_verify_empty_db_is_healthy(self, verifier: AuditVerifier) -> None:
        result = await verifier.run_verification()
        assert isinstance(result, VerificationResult)
        assert result.chain_valid is True
        assert result.anomalies == []

    @pytest.mark.asyncio
    async def test_verify_returns_structured_result(self, verifier: AuditVerifier) -> None:
        result = await verifier.run_verification()
        assert hasattr(result, "chain_valid")
        assert hasattr(result, "chain_message")
        assert hasattr(result, "anomalies")
        assert hasattr(result, "summary")

    @pytest.mark.asyncio
    async def test_verify_detects_high_block_rate(self, audit_db: MagicMock, tmp_path: Path) -> None:
        """If many guardrail blocks in 24h, should report anomaly."""
        from src.audit.db import AuditDB

        db = AuditDB(tmp_path / "audit2.db")
        # Log many blocked tool calls
        for _i in range(20):
            await db.log_tool_call(
                tool_name="suspicious_tool",
                params={},
                allowed=False,
                block_reason="blocked by guardrail",
                session_id="test-session",
            )

        verifier = AuditVerifier(db)
        result = await verifier.run_verification()
        assert len(result.anomalies) > 0
        assert any("block" in a.lower() for a in result.anomalies)

    @pytest.mark.asyncio
    async def test_summary_is_human_readable(self, verifier: AuditVerifier) -> None:
        result = await verifier.run_verification()
        assert isinstance(result.summary, str)
        assert len(result.summary) > 0
