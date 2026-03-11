"""SQLite-backed persistent team registry."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any


class TeamsStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS teams (
                team_id    TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                role       TEXT NOT NULL,
                tools      TEXT NOT NULL,
                monthly_token_budget INTEGER NOT NULL DEFAULT 100000,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                active     INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS team_tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id     TEXT NOT NULL,
                task        TEXT NOT NULL,
                result      TEXT,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                success     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                duration_s  REAL
            );
        """)
        self._conn.commit()

    def create_team(
        self,
        team_id: str,
        name: str,
        role: str,
        tools: list[str],
        monthly_token_budget: int = 100_000,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO teams
               (team_id, name, role, tools, monthly_token_budget)
               VALUES (?, ?, ?, ?, ?)""",
            (team_id, name, role, json.dumps(tools), monthly_token_budget),
        )
        self._conn.commit()

    def get_team(self, team_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM teams WHERE team_id = ?", (team_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_team(row)

    def list_teams(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM teams WHERE active = 1 ORDER BY created_at"
        ).fetchall()
        return [self._row_to_team(r) for r in rows]

    def update_team(
        self,
        team_id: str,
        name: str | None = None,
        role: str | None = None,
        tools: list[str] | None = None,
        monthly_token_budget: int | None = None,
    ) -> bool:
        """Update mutable fields. Returns False if team not found."""
        team = self.get_team(team_id)
        if team is None:
            return False
        new_name = name if name is not None else team["name"]
        new_role = role if role is not None else team["role"]
        new_tools = tools if tools is not None else team["tools"]
        new_budget = monthly_token_budget if monthly_token_budget is not None else team["monthly_token_budget"]
        self._conn.execute(
            """UPDATE teams SET name=?, role=?, tools=?, monthly_token_budget=?
               WHERE team_id=?""",
            (new_name, new_role, json.dumps(new_tools), new_budget, team_id),
        )
        self._conn.commit()
        return True

    def deactivate_team(self, team_id: str) -> None:
        self._conn.execute(
            "UPDATE teams SET active = 0 WHERE team_id = ?", (team_id,)
        )
        self._conn.commit()

    def log_task(
        self,
        team_id: str,
        task: str,
        result: str,
        *,
        tokens_used: int,
        success: bool,
        duration_s: float,
    ) -> None:
        self._conn.execute(
            """INSERT INTO team_tasks (team_id, task, result, tokens_used, success, duration_s)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (team_id, task, result, tokens_used, int(success), duration_s),
        )
        self._conn.commit()

    def monthly_tokens_used(self, team_id: str) -> int:
        row = self._conn.execute(
            """SELECT COALESCE(SUM(tokens_used), 0) AS total
               FROM team_tasks
               WHERE team_id = ?
                 AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')""",
            (team_id,),
        ).fetchone()
        return int(row["total"])

    def team_stats(self, team_id: str) -> dict[str, Any]:
        team = self.get_team(team_id)
        budget = team["monthly_token_budget"] if team else 100_000

        row = self._conn.execute(
            """SELECT
                COUNT(*) AS tasks_total,
                SUM(success) AS tasks_success,
                AVG(duration_s) AS avg_duration,
                COALESCE(SUM(CASE
                    WHEN strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
                    THEN tokens_used ELSE 0 END), 0) AS tokens_month
               FROM team_tasks WHERE team_id = ?""",
            (team_id,),
        ).fetchone()

        tasks_total = int(row["tasks_total"] or 0)
        tasks_success = int(row["tasks_success"] or 0)
        tokens_month = int(row["tokens_month"] or 0)
        success_rate = tasks_success / tasks_total if tasks_total else 0.0
        avg_duration = float(row["avg_duration"] or 0.0)

        return {
            "team_id": team_id,
            "tasks_total": tasks_total,
            "tasks_success": tasks_success,
            "success_rate": success_rate,
            "avg_duration_s": avg_duration,
            "tokens_month": tokens_month,
            "budget_remaining": max(0, budget - tokens_month),
        }

    def all_team_stats(self) -> list[dict[str, Any]]:
        teams = self.list_teams()
        return [self.team_stats(t["team_id"]) for t in teams]

    # ------------------------------------------------------------------
    # Async wrappers — protect concurrent access with asyncio.Lock
    # ------------------------------------------------------------------

    async def create_team_async(
        self,
        team_id: str,
        name: str,
        role: str,
        tools: list[str],
        monthly_token_budget: int = 100_000,
    ) -> None:
        async with self._lock:
            self.create_team(team_id, name, role, tools, monthly_token_budget)

    async def get_team_async(self, team_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return self.get_team(team_id)

    async def list_teams_async(self) -> list[dict[str, Any]]:
        async with self._lock:
            return self.list_teams()

    async def update_team_async(
        self,
        team_id: str,
        name: str | None = None,
        role: str | None = None,
        tools: list[str] | None = None,
        monthly_token_budget: int | None = None,
    ) -> bool:
        async with self._lock:
            return self.update_team(team_id, name, role, tools, monthly_token_budget)

    async def deactivate_team_async(self, team_id: str) -> None:
        async with self._lock:
            self.deactivate_team(team_id)

    async def log_task_async(
        self,
        team_id: str,
        task: str,
        result: str,
        *,
        tokens_used: int,
        success: bool,
        duration_s: float,
    ) -> None:
        async with self._lock:
            self.log_task(
                team_id, task, result,
                tokens_used=tokens_used, success=success, duration_s=duration_s,
            )

    async def monthly_tokens_used_async(self, team_id: str) -> int:
        async with self._lock:
            return self.monthly_tokens_used(team_id)

    async def team_stats_async(self, team_id: str) -> dict[str, Any]:
        async with self._lock:
            return self.team_stats(team_id)

    async def all_team_stats_async(self) -> list[dict[str, Any]]:
        async with self._lock:
            return self.all_team_stats()

    @staticmethod
    def _row_to_team(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["tools"] = json.loads(d["tools"])
        return d
