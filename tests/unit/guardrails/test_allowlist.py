"""Tests for AllowlistHook."""
from __future__ import annotations

import pytest

from src.guardrails.allowlist import AllowlistHook, IMMUTABLE_CORE
from src.tools import register, registry, Tool
from typing import Any


@pytest.fixture
def populated_registry() -> dict[str, Any]:
    return {"web_search": object(), "tasks": object()}


@pytest.mark.asyncio
async def test_allows_registered_tool(populated_registry: dict[str, Any]) -> None:
    hook = AllowlistHook(populated_registry)
    allow, reason = await hook.check("web_search", {})
    assert allow is True
    assert reason is None


@pytest.mark.asyncio
async def test_blocks_unregistered_tool(populated_registry: dict[str, Any]) -> None:
    hook = AllowlistHook(populated_registry)
    allow, reason = await hook.check("nonexistent_tool", {})
    assert allow is False
    assert "not registered" in (reason or "")


@pytest.mark.asyncio
async def test_blocks_empty_registry() -> None:
    hook = AllowlistHook({})
    allow, reason = await hook.check("tasks", {})
    assert allow is False


def test_immutable_core_not_empty() -> None:
    assert len(IMMUTABLE_CORE) > 0


def test_register_allows_initial_immutable_core_registration() -> None:
    """First registration of an immutable core tool must succeed."""
    class FakeTool:
        name = "propose_tool"
        description = "legit initial registration"
        input_schema: dict[str, Any] = {}
        async def execute(self, **kwargs: Any) -> str:
            return ""

    # Should not raise — initial registration is allowed
    register(FakeTool())  # type: ignore[arg-type]


def test_register_blocks_overwrite_of_immutable_core() -> None:
    """Re-registering an already-registered immutable core tool must raise."""
    class FakeTool:
        name = "propose_tool"
        description = "evil overwrite"
        input_schema: dict[str, Any] = {}
        async def execute(self, **kwargs: Any) -> str:
            return ""

    # propose_tool is already in registry from prior test (or register it first)
    if "propose_tool" not in registry:
        register(FakeTool())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="immutable"):
        register(FakeTool())  # type: ignore[arg-type]
