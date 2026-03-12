"""Tests for pipeline quality gates."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.pipeline.gates import (
    STAGE_GATES,
    GateResult,
    GateVerdict,
    check_gate,
)

# ── GateVerdict enum ──────────────────────────────────────────────


class TestGateVerdict:
    def test_has_pass(self) -> None:
        assert GateVerdict.PASS == "pass"

    def test_has_retry(self) -> None:
        assert GateVerdict.RETRY == "retry"

    def test_has_escalate(self) -> None:
        assert GateVerdict.ESCALATE == "escalate"


# ── GateResult dataclass ──────────────────────────────────────────


class TestGateResult:
    def test_frozen(self) -> None:
        result = GateResult(
            verdict=GateVerdict.PASS,
            reason="ok",
            retry_hint="",
            structural_ok=True,
            llm_score=0.0,
        )
        with pytest.raises(FrozenInstanceError):
            result.verdict = GateVerdict.RETRY  # type: ignore[misc]

    def test_has_all_fields(self) -> None:
        result = GateResult(
            verdict=GateVerdict.RETRY,
            reason="too short",
            retry_hint="add more detail",
            structural_ok=False,
            llm_score=0.85,
        )
        assert result.verdict == GateVerdict.RETRY
        assert result.reason == "too short"
        assert result.retry_hint == "add more detail"
        assert result.structural_ok is False
        assert result.llm_score == 0.85


# ── STAGE_GATES dict ─────────────────────────────────────────────


class TestStageGates:
    @pytest.mark.parametrize(
        "stage",
        ["research", "scope", "plan", "implement", "test", "review", "pr"],
    )
    def test_has_all_stages(self, stage: str) -> None:
        assert stage in STAGE_GATES


# ── Helpers ──────────────────────────────────────────────────────


def _pad(text: str, length: int) -> str:
    """Pad text with spaces to reach desired length."""
    if len(text) >= length:
        return text
    return text + " " * (length - len(text))


# ── RESEARCH gate ────────────────────────────────────────────────


class TestResearchGate:
    @pytest.mark.asyncio
    async def test_passes_with_keyword_and_length(self) -> None:
        artifact = _pad("We recommend using approach X for this task.", 200)
        result = await check_gate("research", artifact)
        assert result.verdict == GateVerdict.PASS
        assert result.structural_ok is True

    @pytest.mark.asyncio
    async def test_fails_short_text(self) -> None:
        result = await check_gate("research", "Short recommend text.")
        assert result.verdict == GateVerdict.RETRY
        assert result.structural_ok is False

    @pytest.mark.asyncio
    async def test_fails_without_keywords(self) -> None:
        artifact = _pad("This is a long text with no relevant keywords at all.", 200)
        result = await check_gate("research", artifact)
        assert result.verdict == GateVerdict.RETRY
        assert result.structural_ok is False


# ── SCOPE gate ───────────────────────────────────────────────────


class TestScopeGate:
    @pytest.mark.asyncio
    async def test_passes_with_both_keywords(self) -> None:
        artifact = _pad(
            "The acceptance criteria are clear. This is out of scope: X.",
            300,
        )
        result = await check_gate("scope", artifact)
        assert result.verdict == GateVerdict.PASS
        assert result.structural_ok is True

    @pytest.mark.asyncio
    async def test_passes_with_hyphenated_out_of_scope(self) -> None:
        artifact = _pad(
            "The acceptance criteria are clear. This is out-of-scope: X.",
            300,
        )
        result = await check_gate("scope", artifact)
        assert result.verdict == GateVerdict.PASS

    @pytest.mark.asyncio
    async def test_fails_without_acceptance_criteria(self) -> None:
        artifact = _pad("This is out of scope: nothing here.", 300)
        result = await check_gate("scope", artifact)
        assert result.verdict == GateVerdict.RETRY

    @pytest.mark.asyncio
    async def test_fails_without_out_of_scope(self) -> None:
        artifact = _pad("The acceptance criteria are listed here.", 300)
        result = await check_gate("scope", artifact)
        assert result.verdict == GateVerdict.RETRY


# ── PLAN gate ────────────────────────────────────────────────────


class TestPlanGate:
    @pytest.mark.asyncio
    async def test_passes_with_file_refs_and_test(self) -> None:
        artifact = _pad(
            "We will test the changes in src/foo.py, tests/test_foo.py, and src/bar.py.",
            400,
        )
        result = await check_gate("plan", artifact)
        assert result.verdict == GateVerdict.PASS
        assert result.structural_ok is True

    @pytest.mark.asyncio
    async def test_fails_without_file_refs(self) -> None:
        artifact = _pad("We will test the changes thoroughly.", 400)
        result = await check_gate("plan", artifact)
        assert result.verdict == GateVerdict.RETRY

    @pytest.mark.asyncio
    async def test_fails_without_test_keyword(self) -> None:
        artifact = _pad(
            "We will modify src/foo.py, src/bar.py, and src/baz.py for the task.",
            400,
        )
        result = await check_gate("plan", artifact)
        assert result.verdict == GateVerdict.RETRY


# ── IMPLEMENT gate ───────────────────────────────────────────────


class TestImplementGate:
    @pytest.mark.asyncio
    async def test_passes_with_sufficient_length(self) -> None:
        artifact = _pad("Implementation complete with all changes applied.", 50)
        result = await check_gate("implement", artifact)
        assert result.verdict == GateVerdict.PASS

    @pytest.mark.asyncio
    async def test_fails_short(self) -> None:
        result = await check_gate("implement", "Done.")
        assert result.verdict == GateVerdict.RETRY


# ── TEST gate ────────────────────────────────────────────────────


class TestTestGate:
    @pytest.mark.asyncio
    async def test_passes_with_keyword(self) -> None:
        artifact = _pad("All tests pass. Coverage is at 95%.", 100)
        result = await check_gate("test", artifact)
        assert result.verdict == GateVerdict.PASS

    @pytest.mark.asyncio
    async def test_fails_without_keywords(self) -> None:
        artifact = _pad("Ran the suite and everything looks good.", 100)
        result = await check_gate("test", artifact)
        assert result.verdict == GateVerdict.RETRY


# ── REVIEW gate ──────────────────────────────────────────────────


class TestReviewGate:
    @pytest.mark.asyncio
    async def test_passes_with_keyword(self) -> None:
        artifact = _pad(
            "After reviewing the implementation, my recommendation is to proceed.",
            200,
        )
        result = await check_gate("review", artifact)
        assert result.verdict == GateVerdict.PASS
        assert result.structural_ok is True

    @pytest.mark.asyncio
    async def test_fails_without_keywords(self) -> None:
        artifact = _pad("The code looks fine and has no issues at all.", 200)
        result = await check_gate("review", artifact)
        assert result.verdict == GateVerdict.RETRY


# ── PR gate ──────────────────────────────────────────────────────


class TestPRGate:
    @pytest.mark.asyncio
    async def test_passes_with_url(self) -> None:
        result = await check_gate("pr", "https://github.com/org/repo/pull/42")
        assert result.verdict == GateVerdict.PASS

    @pytest.mark.asyncio
    async def test_fails_without_url(self) -> None:
        result = await check_gate("pr", "PR created successfully #42")
        assert result.verdict == GateVerdict.RETRY


# ── LLM judge (review stage only) ───────────────────────────────


def _make_mock_client(score_text: str) -> Any:
    """Build a mock anthropic client returning the given score text."""
    content_block = MagicMock()
    content_block.text = score_text
    response = MagicMock()
    response.content = [content_block]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


class TestLLMJudge:
    @pytest.mark.asyncio
    async def test_passes_with_high_score(self) -> None:
        client = _make_mock_client("0.85")
        artifact = _pad(
            "After reviewing, my recommendation is go. Code matches plan.",
            200,
        )
        result = await check_gate("review", artifact, client=client, model="test-model")
        assert result.verdict == GateVerdict.PASS
        assert result.llm_score >= 0.7

    @pytest.mark.asyncio
    async def test_retry_with_low_score(self) -> None:
        client = _make_mock_client("0.4")
        artifact = _pad(
            "After reviewing, my recommendation is go. Code matches plan.",
            200,
        )
        result = await check_gate("review", artifact, client=client, model="test-model")
        assert result.verdict == GateVerdict.RETRY
        assert result.llm_score < 0.7
        assert result.reason != ""

    @pytest.mark.asyncio
    async def test_skips_when_client_none(self) -> None:
        artifact = _pad(
            "After reviewing, my recommendation is go. Code matches plan.",
            200,
        )
        result = await check_gate("review", artifact, client=None)
        assert result.verdict == GateVerdict.PASS
        assert result.llm_score == 0.0

    @pytest.mark.asyncio
    async def test_skips_on_client_exception(self) -> None:
        client = AsyncMock()
        client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
        artifact = _pad(
            "After reviewing, my recommendation is go. Code matches plan.",
            200,
        )
        result = await check_gate("review", artifact, client=client, model="test-model")
        assert result.verdict == GateVerdict.PASS
        assert result.llm_score == 0.0


# ── RETRY verdict details ───────────────────────────────────────


class TestRetryVerdict:
    @pytest.mark.asyncio
    async def test_retry_has_hint(self) -> None:
        result = await check_gate("research", "short")
        assert result.verdict == GateVerdict.RETRY
        assert result.retry_hint != ""

    @pytest.mark.asyncio
    async def test_pass_has_structural_ok(self) -> None:
        artifact = _pad("We recommend using approach X.", 200)
        result = await check_gate("research", artifact)
        assert result.verdict == GateVerdict.PASS
        assert result.structural_ok is True


# ── Unknown stage ────────────────────────────────────────────────


class TestUnknownStage:
    @pytest.mark.asyncio
    async def test_unknown_stage_passes(self) -> None:
        result = await check_gate("nonexistent", "anything")
        assert result.verdict == GateVerdict.PASS
