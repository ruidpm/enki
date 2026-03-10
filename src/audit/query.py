"""Query interface for the audit database."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .db import AuditDB
from .integrity import verify_chain


class AuditQuery:
    def __init__(self, db: AuditDB) -> None:
        self._db = db

    def get_security_events(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return Tier 1 security events, optionally filtered."""
        clauses = []
        args: list[Any] = []
        if since:
            clauses.append("timestamp >= ?")
            args.append(since.isoformat())
        if until:
            clauses.append("timestamp <= ?")
            args.append(until.isoformat())
        if session_id:
            clauses.append("session_id = ?")
            args.append(session_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._db._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM tier1 {where} ORDER BY id ASC", args
            ).fetchall()
        return [
            {**dict(row), "data": json.loads(row["data"])} for row in rows
        ]

    def get_session_summary(self, session_id: str) -> dict[str, Any]:
        """Return Tier 2 activity summary for one session."""
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT event_type, data FROM tier2 WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        events = [{"event_type": r["event_type"], "data": json.loads(r["data"])} for r in rows]
        return {"session_id": session_id, "event_count": len(events), "events": events}

    def get_costs(self, since: datetime | None = None) -> dict[str, Any]:
        """Return token + cost breakdown from Tier 2 LLM_CALL events."""
        clauses = ["event_type = 'LLM_CALL'"]
        args: list[Any] = []
        if since:
            clauses.append("timestamp >= ?")
            args.append(since.isoformat())
        with self._db._conn() as conn:
            rows = conn.execute(
                f"SELECT data FROM tier2 WHERE {' AND '.join(clauses)}", args
            ).fetchall()
        total_input = total_output = 0
        total_cost = 0.0
        by_model: dict[str, dict[str, Any]] = {}
        for row in rows:
            d = json.loads(row["data"])
            total_input += d.get("input_tokens", 0)
            total_output += d.get("output_tokens", 0)
            total_cost += d.get("cost_usd", 0.0)
            model = d.get("model", "unknown")
            if model not in by_model:
                by_model[model] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            by_model[model]["input_tokens"] += d.get("input_tokens", 0)
            by_model[model]["output_tokens"] += d.get("output_tokens", 0)
            by_model[model]["cost_usd"] += d.get("cost_usd", 0.0)
        return {
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost_usd": round(total_cost, 6),
            "by_model": by_model,
        }

    def verify_chain(self, session_id: str | None = None) -> tuple[bool, str]:
        """Verify Tier 1 hash chain integrity."""
        events = self.get_security_events(session_id=session_id)
        return verify_chain(events)
