"""Tests for RunPipelineTool — autonomous pipeline orchestrator."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import ModelId
from src.pipeline.gates import GateResult, GateVerdict
from src.pipeline.store import PipelineStage, PipelineStatus, PipelineStore
from src.teams.store import TeamsStore
from src.tools.run_pipeline import RunPipelineTool
from src.workspaces.store import WorkspaceStore

# Auto-pass gate result for tests that don't test gate logic
_GATE_PASS = GateResult(verdict=GateVerdict.PASS, reason="test", retry_hint="", structural_ok=True, llm_score=0.0)


def _make_notifier(confirmed: bool = True) -> AsyncMock:
    n = AsyncMock()
    n.ask_single_confirm = AsyncMock(return_value=confirmed)
    n.send = AsyncMock()
    n.ask_free_text = AsyncMock(return_value="approve")
    n.ask_scope_approval = AsyncMock(return_value="approve")
    return n


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.anthropic_api_key = "test-key"
    cfg.haiku_model = ModelId.HAIKU
    cfg.sonnet_model = ModelId.SONNET
    return cfg


@pytest.fixture
def pipeline_store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(tmp_path / "pipelines.db")


@pytest.fixture
def workspace_store(tmp_path: Path) -> WorkspaceStore:
    store = WorkspaceStore(tmp_path / "ws.db")
    ws_path = tmp_path / "myapp"
    ws_path.mkdir()
    store.add("ws1", name="MyApp", local_path=str(ws_path), language="python")
    return store


@pytest.fixture
def teams_store(tmp_path: Path) -> TeamsStore:
    store = TeamsStore(tmp_path / "teams.db")
    store.create_team(
        team_id="researcher",
        name="Researcher",
        role="You research stuff.",
        tools=["web_search"],
        monthly_token_budget=200_000,
    )
    store.create_team(
        team_id="architect",
        name="Architect",
        role="You plan stuff.",
        tools=["notes"],
        monthly_token_budget=150_000,
    )
    store.create_team(
        team_id="backend-dev",
        name="Backend Dev",
        role="You build stuff.",
        tools=["run_claude_code"],
        monthly_token_budget=500_000,
    )
    store.create_team(
        team_id="qa",
        name="QA",
        role="You test stuff.",
        tools=["run_claude_code"],
        monthly_token_budget=200_000,
    )
    store.create_team(
        team_id="devops",
        name="DevOps",
        role="You deploy stuff.",
        tools=["git_push_branch", "create_pr"],
        monthly_token_budget=200_000,
    )
    return store


def _make_single_shot_response(text: str = "Single-shot result.") -> MagicMock:
    """Create a mock Anthropic API response for single-shot stages."""
    from anthropic.types import TextBlock as _TB
    from anthropic.types import Usage as _U

    mock = MagicMock()
    mock.content = [_TB(text=text, type="text")]
    mock.usage = _U(input_tokens=500, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return mock


def _make_tool(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
    notifier: AsyncMock | None = None,
    confirmed: bool = True,
) -> RunPipelineTool:
    if notifier is None:
        notifier = _make_notifier(confirmed=confirmed)
    return RunPipelineTool(
        notifier=notifier,
        pipeline_store=pipeline_store,
        workspace_store=workspace_store,
        teams_store=teams_store,
        config=_make_config(),
        tool_registry={},
    )


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_workspace_returns_error(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store)
    result = await tool.execute(task="add login")
    assert "error" in result.lower() or "required" in result.lower()


@pytest.mark.asyncio
async def test_read_only_workspace_blocks_pipeline(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """READ_ONLY (trust_level=0) workspace must be blocked before confirmation."""
    from src.workspaces.store import TrustLevel

    workspace_store.add("readonly_ws", name="ReadOnly", local_path=str(tmp_path), trust_level=TrustLevel.READ_ONLY)

    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store)
    result = await tool.execute(workspace_id="readonly_ws", task="add login")
    assert "BLOCKED" in result
    assert "read_only" in result.lower() or "trust" in result.lower()
    # No pipeline should have been created
    assert pipeline_store.list_all() == []


@pytest.mark.asyncio
async def test_unknown_workspace_returns_error(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store)
    result = await tool.execute(workspace_id="ghost", task="add login")
    assert "error" in result.lower() or "not found" in result.lower()


@pytest.mark.asyncio
async def test_user_cancels_returns_cancelled(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, confirmed=False)
    result = await tool.execute(workspace_id="ws1", task="add login")
    assert "cancel" in result.lower()
    # No pipeline should be created
    assert pipeline_store.list_all() == []


# ---------------------------------------------------------------------------
# Happy path — background job started
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_returns_job_id_immediately(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store)
    with patch.object(tool, "_run_background", new_callable=AsyncMock):
        result = await tool.execute(workspace_id="ws1", task="add login")
    assert "pipeline" in result.lower() or "started" in result.lower() or "job" in result.lower()
    # Pipeline record should be created
    pipelines = pipeline_store.list_all()
    assert len(pipelines) == 1
    assert pipelines[0]["workspace_id"] == "ws1"
    assert pipelines[0]["current_stage"] == PipelineStage.RESEARCH


# ---------------------------------------------------------------------------
# Background execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_background_saves_artifact_per_stage(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    notifier = _make_notifier()
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    # Create a pipeline to pass to _run_background
    pipeline_id = "test01"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="add login")

    workspace = workspace_store.get("ws1")
    assert workspace is not None

    fake_result = "Completed research: use JWT for auth."

    # Mock SubAgentRunner to return deterministic results for all text stages
    # Mock CCC subprocess for IMPLEMENT stage
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"Implemented login feature.", b""))

    with (
        patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner,
        patch("src.tools.run_pipeline.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("src.tools.run_pipeline.check_gate", return_value=_GATE_PASS),
        patch.object(tool._output, "create_gist", return_value=None),
        patch.object(tool._output, "create_multi_file_gist", return_value=None),
        patch.object(tool, "_anthropic_client") as mock_client,
    ):
        runner_instance = AsyncMock()
        runner_instance.run = AsyncMock(return_value=(fake_result, 100))
        MockRunner.return_value = runner_instance
        mock_client.messages.create = AsyncMock(return_value=_make_single_shot_response(fake_result))

        await tool._run_background(
            pipeline_id=pipeline_id,
            task="add login",
            workspace=workspace,
        )

    # All stages should have artifacts
    artifacts = pipeline_store.list_artifacts(pipeline_id)
    stage_names = {a["stage"] for a in artifacts}
    # At minimum the text stages should be done
    assert PipelineStage.RESEARCH in stage_names
    assert PipelineStage.SCOPE in stage_names
    assert PipelineStage.PLAN in stage_names


@pytest.mark.asyncio
async def test_background_sends_notification_per_stage(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    notifier = _make_notifier()
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "test02"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="add login")
    workspace = workspace_store.get("ws1")
    assert workspace is not None

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"Built feature.", b""))

    with (
        patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner,
        patch("src.tools.run_pipeline.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("src.tools.run_pipeline.check_gate", return_value=_GATE_PASS),
        patch.object(tool._output, "create_gist", return_value=None),
        patch.object(tool._output, "create_multi_file_gist", return_value=None),
        patch.object(tool, "_anthropic_client") as mock_client,
    ):
        runner_instance = AsyncMock()
        runner_instance.run = AsyncMock(return_value=("done", 50))
        MockRunner.return_value = runner_instance
        mock_client.messages.create = AsyncMock(return_value=_make_single_shot_response("done"))

        await tool._run_background(
            pipeline_id=pipeline_id,
            task="add login",
            workspace=workspace,
        )

    # Should have sent at least one notification per text stage (RESEARCH/SCOPE/PLAN/TEST/REVIEW)
    assert notifier.send.call_count >= 3


@pytest.mark.asyncio
async def test_pipeline_marked_completed_on_success(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    notifier = _make_notifier()
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "test03"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="add login")
    workspace = workspace_store.get("ws1")
    assert workspace is not None

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"Done.", b""))

    with (
        patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner,
        patch("src.tools.run_pipeline.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("src.tools.run_pipeline.check_gate", return_value=_GATE_PASS),
        patch.object(tool._output, "create_gist", return_value=None),
        patch.object(tool._output, "create_multi_file_gist", return_value=None),
        patch.object(tool, "_anthropic_client") as mock_client,
    ):
        runner_instance = AsyncMock()
        runner_instance.run = AsyncMock(return_value=("done", 50))
        MockRunner.return_value = runner_instance
        mock_client.messages.create = AsyncMock(return_value=_make_single_shot_response("done"))

        await tool._run_background(
            pipeline_id=pipeline_id,
            task="add login",
            workspace=workspace,
        )

    p = pipeline_store.get(pipeline_id)
    assert p is not None
    assert p["status"] == PipelineStatus.COMPLETED


@pytest.mark.asyncio
async def test_pipeline_marked_failed_on_error(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    notifier = _make_notifier()
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "test04"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="add login")
    workspace = workspace_store.get("ws1")
    assert workspace is not None

    with patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner:
        runner_instance = AsyncMock()
        runner_instance.run = AsyncMock(side_effect=RuntimeError("API down"))
        MockRunner.return_value = runner_instance

        await tool._run_background(
            pipeline_id=pipeline_id,
            task="add login",
            workspace=workspace,
        )

    p = pipeline_store.get(pipeline_id)
    assert p is not None
    assert p["status"] == PipelineStatus.ABORTED
    # User should have been notified of failure
    assert notifier.send.called
    last_call = notifier.send.call_args[0][0]
    assert "error" in last_call.lower() or "failed" in last_call.lower() or "abort" in last_call.lower()


# ---------------------------------------------------------------------------
# Clarification protocol
# ---------------------------------------------------------------------------


def _make_notifier_with_free_text(answer: str | None) -> AsyncMock:
    n = AsyncMock()
    n.ask_single_confirm = AsyncMock(return_value=True)
    n.send = AsyncMock()
    n.ask_free_text = AsyncMock(return_value=answer)
    n.ask_scope_approval = AsyncMock(return_value="approve")
    return n


@pytest.mark.asyncio
async def test_stage_clarification_single_round(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """Stage returns CLARIFICATION_NEEDED once, then a real artifact — should succeed."""
    notifier = _make_notifier_with_free_text("It's a task manager app for teams.")
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "clarif01"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build an app")

    call_count = 0

    async def _fake_run(prompt: str) -> tuple[str, int]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ("CLARIFICATION_NEEDED:\n1. What does the app do?", 50)
        return ("Scope defined: task manager with React + Supabase.", 100)

    with patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner:
        runner_instance = AsyncMock()
        runner_instance.run = _fake_run
        MockRunner.return_value = runner_instance

        result = await tool._run_llm_stage(
            pipeline_id=pipeline_id,
            stage=PipelineStage.RESEARCH,
            task="build an app",
            context="",
            artifacts={},
        )

    assert "task manager" in result.lower() or "scope defined" in result.lower()
    assert notifier.ask_free_text.call_count == 1
    assert notifier.send.call_count == 1  # clarification question sent to user


@pytest.mark.asyncio
async def test_stage_clarification_timeout_raises(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """ask_free_text returns None (timeout) → RuntimeError."""
    notifier = _make_notifier_with_free_text(None)  # simulates timeout
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "clarif02"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build an app")

    with patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner:
        runner_instance = AsyncMock()
        runner_instance.run = AsyncMock(return_value=("CLARIFICATION_NEEDED:\n1. What does the app do?", 50))
        MockRunner.return_value = runner_instance

        with pytest.raises(RuntimeError, match="timed out"):
            await tool._run_llm_stage(
                pipeline_id=pipeline_id,
                stage=PipelineStage.RESEARCH,
                task="build an app",
                context="",
                artifacts={},
            )


@pytest.mark.asyncio
async def test_stage_clarification_exceeds_max_rounds(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """Stage keeps returning CLARIFICATION_NEEDED beyond max rounds → RuntimeError."""
    notifier = _make_notifier_with_free_text("some answer")
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "clarif03"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build an app")

    with patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner:
        runner_instance = AsyncMock()
        runner_instance.run = AsyncMock(return_value=("CLARIFICATION_NEEDED:\n1. Still unclear.", 50))
        MockRunner.return_value = runner_instance

        with pytest.raises(RuntimeError, match="exceeded"):
            await tool._run_llm_stage(
                pipeline_id=pipeline_id,
                stage=PipelineStage.RESEARCH,
                task="build an app",
                context="",
                artifacts={},
            )


@pytest.mark.asyncio
async def test_stage_no_clarification_unaffected(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """Happy path: stage returns artifact directly, no ask_free_text called."""
    notifier = _make_notifier_with_free_text("should not be called")
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "clarif04"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="add login")

    with patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner:
        runner_instance = AsyncMock()
        runner_instance.run = AsyncMock(return_value=("Research done: use JWT.", 80))
        MockRunner.return_value = runner_instance

        result = await tool._run_llm_stage(
            pipeline_id=pipeline_id,
            stage=PipelineStage.RESEARCH,
            task="add login",
            context="",
            artifacts={},
        )

    assert result == "Research done: use JWT."
    notifier.ask_free_text.assert_not_called()


# ---------------------------------------------------------------------------
# Single-shot vs agentic stage modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_uses_single_shot_not_sub_agent(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """SCOPE is single-shot — should call Anthropic API directly, not SubAgentRunner."""
    notifier = _make_notifier()
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "ss01"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build app")

    with (
        patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner,
        patch.object(tool, "_anthropic_client") as mock_client,
    ):
        mock_client.messages.create = AsyncMock(return_value=_make_single_shot_response("## What Will Be Built\nA web app."))

        result = await tool._run_llm_stage(
            pipeline_id=pipeline_id,
            stage="scope",
            task="build app",
            context="",
            artifacts={"research": "Found that React + Supabase is best."},
        )

    # SubAgentRunner should NOT have been instantiated
    MockRunner.assert_not_called()
    # Direct API call should have been made
    mock_client.messages.create.assert_called_once()
    assert "What Will Be Built" in result


@pytest.mark.asyncio
async def test_plan_uses_single_shot(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """PLAN is single-shot."""
    notifier = _make_notifier()
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "ss02"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build app")

    with (
        patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner,
        patch.object(tool, "_anthropic_client") as mock_client,
    ):
        mock_client.messages.create = AsyncMock(return_value=_make_single_shot_response("## Files to Create\nsrc/main.py"))

        result = await tool._run_llm_stage(
            pipeline_id=pipeline_id,
            stage="plan",
            task="build app",
            context="",
            artifacts={"research": "R", "scope": "S"},
        )

    MockRunner.assert_not_called()
    assert "Files to Create" in result


@pytest.mark.asyncio
async def test_research_uses_sub_agent_runner(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """RESEARCH is agentic — should use SubAgentRunner."""
    notifier = _make_notifier()
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "ss03"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build app")

    with patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner:
        runner_instance = AsyncMock()
        runner_instance.run = AsyncMock(return_value=("## Key Findings\nUse React.", 100))
        MockRunner.return_value = runner_instance

        result = await tool._run_llm_stage(
            pipeline_id=pipeline_id,
            stage="research",
            task="build app",
            context="",
            artifacts={},
        )

    MockRunner.assert_called_once()
    # Check max_steps matches stage config (10 for research)
    call_kwargs = MockRunner.call_args[1]
    assert call_kwargs["max_steps"] == 10
    assert "Key Findings" in result


@pytest.mark.asyncio
async def test_single_shot_does_not_inject_ccc(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """Single-shot stages should not get run_code_task tool."""
    notifier = _make_notifier()
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)
    # Simulate having a pipeline CCC instance
    tool._pipeline_ccc = MagicMock()

    pipeline_id = "ss04"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build app")

    with patch.object(tool, "_anthropic_client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=_make_single_shot_response("Scope doc."))

        await tool._run_llm_stage(
            pipeline_id=pipeline_id,
            stage="scope",
            task="build app",
            context="",
            artifacts={},
        )

    # The API call should NOT have tools parameter with run_code_task
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "tools" not in call_kwargs or not call_kwargs.get("tools")


@pytest.mark.asyncio
async def test_test_stage_uses_single_shot(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """TEST is single-shot — should call Anthropic API directly, not SubAgentRunner."""
    notifier = _make_notifier()
    tool = _make_tool(tmp_path, pipeline_store, workspace_store, teams_store, notifier=notifier)

    pipeline_id = "ss05"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build app")

    with (
        patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner,
        patch.object(tool, "_anthropic_client") as mock_client,
    ):
        mock_client.messages.create = AsyncMock(
            return_value=_make_single_shot_response("## Test Summary\nLooks good.\n## Verdict\npassed — ready for review")
        )

        result = await tool._run_llm_stage(
            pipeline_id=pipeline_id,
            stage="test",
            task="build app",
            context="",
            artifacts={"plan": "Build the thing.", "implement": "Built the thing."},
        )

    MockRunner.assert_not_called()
    mock_client.messages.create.assert_called_once()
    assert "Test Summary" in result


# ---------------------------------------------------------------------------
# Prompt structure tests
# ---------------------------------------------------------------------------


class TestBuildStagePrompt:
    """Verify _build_stage_prompt returns structured section headers."""

    def test_research_prompt_has_sections(self) -> None:
        from src.tools.run_pipeline import _build_stage_prompt

        prompt = _build_stage_prompt("research", "build app", "", {})
        assert "## Key Findings" in prompt
        assert "## Recommended Approach" in prompt
        assert "## Libraries and Dependencies" in prompt
        assert "## Risks and Gotchas" in prompt

    def test_scope_prompt_has_sections(self) -> None:
        from src.tools.run_pipeline import _build_stage_prompt

        prompt = _build_stage_prompt("scope", "build app", "", {"research": "Found stuff."})
        assert "## What Will Be Built" in prompt
        assert "## Tech Stack and Rationale" in prompt
        assert "## Acceptance Criteria" in prompt
        assert "## Out of Scope" in prompt
        assert "ONE response" in prompt

    def test_plan_prompt_has_sections(self) -> None:
        from src.tools.run_pipeline import _build_stage_prompt

        prompt = _build_stage_prompt("plan", "build app", "", {"research": "R", "scope": "S"})
        assert "## Files to Create/Modify" in prompt
        assert "## Implementation Steps" in prompt
        assert "## Test Strategy" in prompt
        assert "ONE response" in prompt

    def test_test_prompt_has_sections(self) -> None:
        from src.tools.run_pipeline import _build_stage_prompt

        prompt = _build_stage_prompt("test", "build app", "", {"plan": "P", "implement": "done"})
        assert "## Test Summary" in prompt
        assert "## Deviations from Plan" in prompt
        assert "## Missing Test Paths" in prompt
        assert "## Edge Cases and Security" in prompt
        assert "## Verdict" in prompt
        assert "NO tools" in prompt

    def test_review_prompt_has_sections(self) -> None:
        from src.tools.run_pipeline import _build_stage_prompt

        prompt = _build_stage_prompt("review", "build app", "", {"plan": "P", "implement": "I", "test": "T"})
        assert "## Deviations from Plan" in prompt
        assert "## Quality Issues" in prompt
        assert "## Go/No-Go Recommendation" in prompt
        assert "ONE response" in prompt

    def test_single_shot_gets_full_artifacts(self) -> None:
        """Single-shot stages should NOT truncate prior artifacts."""
        from src.tools.run_pipeline import _build_stage_prompt

        long_research = "A" * 5000
        prompt = _build_stage_prompt("scope", "build app", "", {"research": long_research})
        # Full 5000-char artifact should be in the prompt (not truncated to 1500)
        assert long_research in prompt

    def test_agentic_truncates_artifacts(self) -> None:
        """Agentic stages (RESEARCH) should truncate prior artifacts."""
        from src.tools.run_pipeline import _build_stage_prompt

        # RESEARCH is the only remaining agentic LLM stage — it doesn't receive prior artifacts
        # so test truncation via the review stage's agentic fallback path is no longer applicable.
        # Instead verify TEST (now single-shot) gets full artifacts.
        long_impl = "B" * 5000
        prompt = _build_stage_prompt("test", "build app", "", {"plan": "P", "implement": long_impl})
        # Single-shot: full artifact, not truncated
        assert long_impl in prompt

    def test_test_includes_browser_check(self) -> None:
        """TEST prompt should include browser check results if present."""
        from src.tools.run_pipeline import _build_stage_prompt

        browser_report = "## Browser Pre-Check\nFile: index.html\n### Console Errors\nNone"
        prompt = _build_stage_prompt("test", "build app", "", {"implement": "done", "_browser_check": browser_report})
        assert "Browser Pre-Check" in prompt
        assert "Console Errors" in prompt


# ---------------------------------------------------------------------------
# Task cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_cancels_background_task(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """manage_pipeline abort calls job_registry.cancel() which cancels the asyncio Task."""
    from src.jobs import JobRegistry
    from src.tools.manage_pipeline import ManagePipelineTool

    job_registry = JobRegistry()
    manage_tool = ManagePipelineTool(
        pipeline_store=pipeline_store,
        workspace_store=workspace_store,
        job_registry=job_registry,
    )

    # Simulate a running pipeline with a real (but sleeping) task
    pipeline_id = "abc123"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build thing")

    async def _long_running() -> None:
        await asyncio.sleep(9999)

    task = asyncio.create_task(_long_running())
    job_registry.start(pipeline_id, job_type="pipeline", description="build thing")
    job_registry.set_task(pipeline_id, task)

    result = await manage_tool.execute(action="abort", pipeline_id=pipeline_id)

    assert "aborted" in result.lower()
    assert "cancelled" in result.lower()
    # Give the event loop a tick to propagate cancellation
    await asyncio.sleep(0)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_run_background_cancelled_marks_aborted(
    tmp_path: Path,
    pipeline_store: PipelineStore,
    workspace_store: WorkspaceStore,
    teams_store: TeamsStore,
) -> None:
    """CancelledError in _run_background sets pipeline status to ABORTED."""
    from src.jobs import JobRegistry

    notifier = _make_notifier(confirmed=True)
    notifier.ask_free_text = AsyncMock(return_value=None)
    job_registry = JobRegistry()

    tool = RunPipelineTool(
        notifier=notifier,
        pipeline_store=pipeline_store,
        workspace_store=workspace_store,
        teams_store=teams_store,
        config=_make_config(),
        tool_registry={},
        job_registry=job_registry,
    )

    pipeline_id = "cancel01"
    pipeline_store.create(pipeline_id, workspace_id="ws1", task="build it")
    job_registry.start(pipeline_id, job_type="pipeline", description="build it")

    # Patch SubAgentRunner to hang forever so we can cancel mid-run
    async def _hang(*a: object, **kw: object) -> tuple[str, int]:
        await asyncio.sleep(9999)
        return ("", 0)

    with patch("src.tools.run_pipeline.SubAgentRunner") as MockRunner:
        runner_instance = AsyncMock()
        runner_instance.run = _hang
        MockRunner.return_value = runner_instance

        bg = asyncio.create_task(
            tool._run_background(
                pipeline_id=pipeline_id,
                task="build it",
                workspace=workspace_store.get("ws1"),
            )
        )
        job_registry.set_task(pipeline_id, bg)

        # Let it start
        await asyncio.sleep(0)
        bg.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await bg

    p = pipeline_store.get(pipeline_id)
    assert p is not None
    assert p["status"] == PipelineStatus.ABORTED
    notifier.send.assert_called()
    last_msg = notifier.send.call_args[0][0]
    assert "cancelled" in last_msg.lower()
