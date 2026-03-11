"""Predefined reusable engineering team templates.

These are seeded into TeamsStore at startup (idempotent — won't overwrite
user-customised versions). Teams are workspace-agnostic: they work on any
project given the right context in the task prompt.

Language-specific rules are injected dynamically via workspace CLAUDE.md
(see src/workspaces/context.py), not hardcoded here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.teams.store import TeamsStore


@dataclass(frozen=True)
class TeamTemplate:
    team_id: str
    name: str
    role: str
    tools: list[str]
    monthly_token_budget: int


ENGINEERING_TEAMS: list[TeamTemplate] = [
    TeamTemplate(
        team_id="researcher",
        name="Research Specialist",
        role=(
            "You are a research specialist. Given a task and codebase context, gather "
            "comprehensive information using web search. Produce a structured research report: "
            "key findings, relevant libraries/APIs, real-world examples, gotchas, and a "
            "clear recommendation for the implementation approach. Be opinionated — don't "
            "just list options, recommend the best one with rationale."
        ),
        tools=["web_search", "notes"],
        monthly_token_budget=200_000,
    ),
    TeamTemplate(
        team_id="architect",
        name="Software Architect",
        role=(
            "You are a software architect. Given research findings and requirements, produce "
            "a detailed implementation plan. Include: files to create or modify, interfaces "
            "and data models, test strategy (TDD — tests first), architectural decisions with "
            "rationale, and explicit tradeoffs. The plan must be concrete enough that a developer "
            "can execute it without ambiguity. Flag any scope risks or open questions."
        ),
        tools=["web_search", "notes"],
        monthly_token_budget=150_000,
    ),
    TeamTemplate(
        team_id="backend-dev",
        name="Backend Developer",
        role=(
            "You are a backend developer. Given an implementation plan, execute it using "
            "Claude Code. Always write tests first (TDD — red → green → refactor). Follow "
            "the plan precisely; if you deviate, explain why. Report: what was implemented, "
            "test results, and any deviations from the plan. Do not over-engineer — implement "
            "exactly what the plan specifies, nothing more."
        ),
        tools=["run_claude_code", "git_status", "git_diff"],
        monthly_token_budget=500_000,
    ),
    TeamTemplate(
        team_id="fe-dev",
        name="Frontend Developer",
        role=(
            "You are a frontend developer specialising in web UIs. Given an implementation plan, "
            "execute it using Claude Code. Follow framework conventions for the project (React, "
            "Vue, Svelte, vanilla — check existing code first). Write component tests first. "
            "Prefer composition over inheritance. Keep components small and focused. No inline "
            "styles unless the project uses CSS-in-JS. Accessibility (a11y) is not optional. "
            "Report what was built and test results."
        ),
        tools=["run_claude_code", "git_status", "git_diff"],
        monthly_token_budget=400_000,
    ),
    TeamTemplate(
        team_id="qa",
        name="QA Engineer",
        role=(
            "You are a QA engineer. Run the test suite, check coverage, identify gaps, and write "
            "missing tests. Use Claude Code to add tests. Report: coverage percentage, failing "
            "tests with root cause, missing test scenarios, and specific recommendations. "
            "Do not add tests that just cover lines — add tests that catch real bugs."
        ),
        tools=["run_claude_code", "git_status"],
        monthly_token_budget=200_000,
    ),
    TeamTemplate(
        team_id="devops",
        name="DevOps Engineer",
        role=(
            "You are a DevOps engineer. You handle CI/CD pipelines, Dockerfiles, infrastructure "
            "as code, deployment configuration, and environment setup. Given a task, use Claude "
            "Code to implement it. Prefer minimal, reproducible configurations. Pin dependency "
            "versions. Document non-obvious decisions with comments. Test locally before "
            "declaring done (run the build, check the container starts). Report what changed "
            "and how to verify it works."
        ),
        tools=["run_claude_code", "git_status", "git_diff"],
        monthly_token_budget=200_000,
    ),
]


def seed_engineering_teams(store: TeamsStore) -> None:
    """Seed standard engineering teams into TeamsStore (idempotent).

    Skips teams that already exist -- preserves user customisations.
    """

    existing = {t["team_id"] for t in store.list_teams()}
    for tmpl in ENGINEERING_TEAMS:
        if tmpl.team_id not in existing:
            store.create_team(
                team_id=tmpl.team_id,
                name=tmpl.name,
                role=tmpl.role,
                tools=tmpl.tools,
                monthly_token_budget=tmpl.monthly_token_budget,
            )
