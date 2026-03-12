"""Team performance report — pure SQL aggregation, zero LLM calls."""

from __future__ import annotations

from typing import Any

from src.teams.store import TeamsStore


class TeamReportTool:
    name = "team_report"
    description = (
        "Get status and performance metrics for all teams or a specific team. "
        "Returns task counts, success rates, token usage, and budget remaining. "
        "No LLM call — instant SQL query."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "Optional: specific team ID. Omit to see all teams.",
            },
        },
    }

    def __init__(self, store: TeamsStore) -> None:
        self._store = store

    async def execute(self, **kwargs: Any) -> str:
        team_id: str | None = kwargs.get("team_id")

        if team_id:
            team = self._store.get_team(team_id)
            if team is None:
                return f"[ERROR] Team '{team_id}' not found."
            stats = [self._store.team_stats(team_id)]
            teams_by_id = {team_id: team}
        else:
            all_teams = self._store.list_teams()
            if not all_teams:
                return "No active teams registered. Use spawn_team to create teams."
            stats = self._store.all_team_stats()
            teams_by_id = {t["team_id"]: t for t in all_teams}

        return self._format_report(stats, teams_by_id)

    @staticmethod
    def _format_report(
        stats: list[dict[str, Any]],
        teams_by_id: dict[str, dict[str, Any]],
    ) -> str:
        lines = ["## Team Report\n"]
        lines.append("| Team | Name | Tasks | Success | Avg dur | Tokens/mo | Budget left |")
        lines.append("|------|------|-------|---------|---------|-----------|-------------|")

        for s in stats:
            tid = s["team_id"]
            name = teams_by_id.get(tid, {}).get("name", tid)
            success_pct = f"{s['success_rate'] * 100:.0f}%"
            avg_dur = f"{s['avg_duration_s']:.1f}s"
            lines.append(
                f"| {tid} | {name} | {s['tasks_total']} | {success_pct} "
                f"| {avg_dur} | {s['tokens_month']:,} | {s['budget_remaining']:,} |"
            )

        return "\n".join(lines)
