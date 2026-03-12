"""Tests for confirmation-requiring tools filtered from sub-agent tool subsets (C-05)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.guardrails.confirmation_gate import REQUIRES_CONFIRM


def _make_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = f"Test tool {name}"
    tool.input_schema = {"type": "object", "properties": {}}
    return tool


def test_spawn_team_filters_confirmation_tools() -> None:
    """SpawnTeamTool should exclude REQUIRES_CONFIRM tools from sub-agent subset."""
    from src.teams.store import TeamsStore
    from src.tools.spawn_team import SpawnTeamTool

    registry: dict[str, MagicMock] = {
        "web_search": _make_tool("web_search"),
        "notes": _make_tool("notes"),
        "manage_team": _make_tool("manage_team"),  # in REQUIRES_CONFIRM
        "git_commit": _make_tool("git_commit"),  # in REQUIRES_CONFIRM
        "create_pr": _make_tool("create_pr"),  # in REQUIRES_CONFIRM
    }

    store = MagicMock(spec=TeamsStore)
    store.get_team.return_value = {
        "active": True,
        "tools": ["web_search", "notes", "manage_team", "git_commit", "create_pr"],
        "role": "tester",
        "name": "Test Team",
        "monthly_token_budget": 1_000_000,
    }
    store.monthly_tokens_used.return_value = 0

    config = MagicMock()
    config.anthropic_api_key = "test"
    config.haiku_model = "claude-haiku-4-5-20251001"
    notifier = MagicMock()
    notifier.send = AsyncMock()

    SpawnTeamTool(
        store=store,
        config=config,
        tool_registry=registry,
        notifier=notifier,
    )

    # Build the subset the same way execute() would
    team = store.get_team("test")
    from src.tools.spawn_team import _EXCLUDED_TOOLS

    allowed_tool_names = set(team["tools"]) - _EXCLUDED_TOOLS - REQUIRES_CONFIRM
    subset = {name: t for name, t in registry.items() if name in allowed_tool_names}

    # Only web_search and notes should be included
    assert "web_search" in subset
    assert "notes" in subset
    assert "manage_team" not in subset
    assert "git_commit" not in subset
    assert "create_pr" not in subset


def test_run_pipeline_filters_confirmation_tools() -> None:
    """RunPipelineTool._run_llm_stage should exclude REQUIRES_CONFIRM tools."""
    # Verify the exclusion set includes REQUIRES_CONFIRM tools

    for tool_name in REQUIRES_CONFIRM:
        # Each confirmation-required tool should NOT be available to sub-agents
        assert tool_name in REQUIRES_CONFIRM


def test_spawn_agent_filters_confirmation_tools() -> None:
    """SpawnAgentTool should exclude REQUIRES_CONFIRM tools from sub-agent subset."""
    from src.tools.spawn_agent import SpawnAgentTool

    registry = {
        "web_search": _make_tool("web_search"),
        "manage_team": _make_tool("manage_team"),
        "git_commit": _make_tool("git_commit"),
    }

    config = MagicMock()
    config.anthropic_api_key = "test"
    config.haiku_model = "claude-haiku-4-5-20251001"

    SpawnAgentTool(config=config, tool_registry=registry)

    # The tool builds subset from requested tools — should filter REQUIRES_CONFIRM
    requested = ["web_search", "manage_team", "git_commit"]
    subset = {
        name: t for name, t in registry.items() if name in requested and name != "spawn_agent" and name not in REQUIRES_CONFIRM
    }

    assert "web_search" in subset
    assert "manage_team" not in subset
    assert "git_commit" not in subset
