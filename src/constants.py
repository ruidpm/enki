"""Project-wide constants — shared by tools and guardrails.

Avoids circular dependencies between layers (tools should not import from guardrails).
"""

from __future__ import annotations

# Tools that must be confirmed before execution
REQUIRES_CONFIRM: frozenset[str] = frozenset(
    {
        "create_task",
        "update_task",
        "delete_task",
        "git_commit",
        "git_push_branch",
        "create_pr",
        "request_restart",
        "propose_tool",
        "remove_tool",
        "manage_team",
        "manage_schedule",
        "manage_workspace",
    }
)
