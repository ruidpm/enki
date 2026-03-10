"""Manage persistent team agents — create, update, deactivate.

All three actions require confirmation via the guardrail confirmation gate.
"""
from __future__ import annotations

from typing import Any

import structlog

from src.teams.store import TeamsStore

log = structlog.get_logger()


class ManageTeamTool:
    name = "manage_team"
    description = (
        "Create, update, or deactivate a persistent team agent. "
        "Actions: 'create' (hire a new team), 'update' (change role/tools/budget), "
        "'deactivate' (retire/fire a team). "
        "All actions require user confirmation. Use team_report to see current teams."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "update", "deactivate"],
                "description": "What to do: create a new team, update an existing one, or deactivate (fire) one.",
            },
            "team_id": {
                "type": "string",
                "description": "Unique team identifier (slug, e.g. 'researcher', 'engineer')",
            },
            "name": {
                "type": "string",
                "description": "Human-readable team name (create/update)",
            },
            "role": {
                "type": "string",
                "description": "System prompt defining the team's specialization and behavior (create/update)",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tool names the team may use (create/update)",
            },
            "monthly_token_budget": {
                "type": "integer",
                "description": "Monthly token budget (default 100000). create/update only.",
            },
        },
        "required": ["action", "team_id"],
    }

    def __init__(self, store: TeamsStore) -> None:
        self._store = store

    async def execute(self, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "")
        team_id: str | None = kwargs.get("team_id")

        if not team_id:
            return "[ERROR] team_id is required."

        if action == "create":
            return self._create(team_id, kwargs)
        elif action == "update":
            return self._update(team_id, kwargs)
        elif action == "deactivate":
            return self._deactivate(team_id)
        else:
            return f"[ERROR] Unknown action '{action}'. Use create, update, or deactivate."

    def _create(self, team_id: str, kwargs: dict[str, Any]) -> str:
        name: str = kwargs.get("name") or team_id
        role: str = kwargs.get("role") or "You are a helpful assistant."
        tools: list[str] = kwargs.get("tools") or []
        budget: int = int(kwargs.get("monthly_token_budget") or 100_000)

        self._store.create_team(
            team_id=team_id,
            name=name,
            role=role,
            tools=tools,
            monthly_token_budget=budget,
        )
        log.info("team_created", team_id=team_id, name=name, tools=tools)
        return (
            f"Team '{team_id}' created.\n"
            f"Name: {name}\n"
            f"Role: {role}\n"
            f"Tools: {', '.join(tools) or 'none'}\n"
            f"Monthly budget: {budget:,} tokens"
        )

    def _update(self, team_id: str, kwargs: dict[str, Any]) -> str:
        existing = self._store.get_team(team_id)
        if existing is None:
            return f"[ERROR] Team '{team_id}' not found."

        name: str | None = kwargs.get("name")
        role: str | None = kwargs.get("role")
        tools: list[str] | None = kwargs.get("tools")
        budget_raw = kwargs.get("monthly_token_budget")
        budget: int | None = int(budget_raw) if budget_raw is not None else None

        self._store.update_team(
            team_id=team_id,
            name=name,
            role=role,
            tools=tools,
            monthly_token_budget=budget,
        )
        log.info("team_updated", team_id=team_id)
        updated = self._store.get_team(team_id)
        assert updated is not None
        return (
            f"Team '{team_id}' updated.\n"
            f"Name: {updated['name']}\n"
            f"Role: {updated['role']}\n"
            f"Tools: {', '.join(updated['tools']) or 'none'}\n"
            f"Monthly budget: {updated['monthly_token_budget']:,} tokens"
        )

    def _deactivate(self, team_id: str) -> str:
        existing = self._store.get_team(team_id)
        if existing is None:
            return f"[ERROR] Team '{team_id}' not found."
        self._store.deactivate_team(team_id)
        log.info("team_deactivated", team_id=team_id)
        return f"Team '{team_id}' ({existing['name']}) has been deactivated."
