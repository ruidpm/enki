"""Tests for agent tool dispatch and guardrail integration."""
from __future__ import annotations

from typing import Any

import pytest

from src.guardrails import GuardrailChain
from src.guardrails.allowlist import AllowlistHook


class FakeTool:
    name = "fake_tool"
    description = "a test tool"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "fake result"


@pytest.fixture
def fake_registry() -> dict[str, Any]:
    t = FakeTool()
    return {t.name: t}


@pytest.fixture
def chain(fake_registry: dict[str, Any]) -> GuardrailChain:
    return GuardrailChain([AllowlistHook(fake_registry)])


@pytest.mark.asyncio
async def test_allowed_tool_executes(
    fake_registry: dict[str, Any], chain: GuardrailChain
) -> None:
    allow, reason = await chain.run("fake_tool", {})
    assert allow is True
    result = await fake_registry["fake_tool"].execute()
    assert result == "fake result"


@pytest.mark.asyncio
async def test_blocked_tool_does_not_execute(chain: GuardrailChain) -> None:
    executed = False

    async def should_not_run(**kwargs: Any) -> str:
        nonlocal executed
        executed = True
        return "bad"

    allow, reason = await chain.run("unregistered", {})
    assert allow is False
    assert not executed


@pytest.mark.asyncio
async def test_guardrail_reason_returned_on_block(chain: GuardrailChain) -> None:
    allow, reason = await chain.run("unregistered", {})
    assert allow is False
    assert reason is not None
    assert len(reason) > 0
