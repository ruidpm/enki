"""Tests for critical production fixes.

Covers:
1. SQLite WAL mode on all databases
2. Anthropic API retry/timeout
3. Graceful shutdown — pending task cancellation
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# 1. SQLite WAL mode
# ---------------------------------------------------------------------------


class TestSQLiteWALMode:
    """Every database should use WAL journal mode for crash safety."""

    def test_audit_db_uses_wal(self, tmp_path: Path) -> None:
        from src.audit.db import AuditDB

        db = AuditDB(tmp_path / "audit.db")
        with db._conn() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_memory_store_uses_wal(self, tmp_path: Path) -> None:
        from src.memory.store import MemoryStore

        store = MemoryStore(
            tmp_path / "memory.db",
            logs_dir=tmp_path / "logs",
            facts_path=tmp_path / "facts.md",
        )
        with store._conn() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_teams_store_uses_wal(self, tmp_path: Path) -> None:
        from src.teams.store import TeamsStore

        store = TeamsStore(tmp_path / "teams.db")
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_pipeline_store_uses_wal(self, tmp_path: Path) -> None:
        from src.pipeline.store import PipelineStore

        store = PipelineStore(tmp_path / "pipelines.db")
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_workspace_store_uses_wal(self, tmp_path: Path) -> None:
        from src.workspaces.store import WorkspaceStore

        store = WorkspaceStore(tmp_path / "workspaces.db")
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_schedule_store_uses_wal(self, tmp_path: Path) -> None:
        from src.schedule.store import ScheduleStore

        store = ScheduleStore(tmp_path / "schedule.db")
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


# ---------------------------------------------------------------------------
# 2. API retry/timeout
# ---------------------------------------------------------------------------


class TestAPIRetryTimeout:
    """Agent should retry on transient API failures and timeout on hangs."""

    @pytest.mark.asyncio
    async def test_agent_retries_on_api_error(self) -> None:
        """Agent should retry up to 3 times on APIConnectionError."""
        import anthropic

        from src.agent import Agent

        config = MagicMock()
        config.default_model = "claude-sonnet"
        config.haiku_model = "claude-haiku"
        config.opus_model = "claude-opus"
        config.anthropic_api_key = "test-key"
        config.max_autonomous_turns = 5
        config.session_timeout_hours = 24
        config.max_context_tokens = 120_000

        agent = Agent(
            config=config,
            guardrails=MagicMock(),
            memory=MagicMock(build_context=MagicMock(return_value=""), append_turn=MagicMock()),
            tool_registry={},
            audit=MagicMock(log_tier2=AsyncMock(), log_tool_call=AsyncMock()),
            cost_guard=MagicMock(
                daily_cost_usd=0.0,
                monthly_cost_usd=0.0,
                session_tokens=0,
                record_llm_call=MagicMock(),
                record_autonomous_turn=MagicMock(),
                on_user_message=MagicMock(),
                reset_session=MagicMock(),
            ),
            loop_detector=MagicMock(set_session=MagicMock(), on_user_message=MagicMock()),
            rate_limiter=MagicMock(reset=MagicMock()),
        )

        # First 2 calls fail, third succeeds
        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [MagicMock(text="Hello", type="text")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        agent._client.messages.create = AsyncMock(
            side_effect=[
                anthropic.APIConnectionError(request=MagicMock()),
                anthropic.APIConnectionError(request=MagicMock()),
                mock_response,
            ]
        )

        result = await agent.run_turn("test")
        assert result == "Hello"
        assert agent._client.messages.create.call_count == 3

    @pytest.mark.asyncio
    async def test_agent_gives_up_after_max_retries(self) -> None:
        """Agent should return error message after exhausting retries."""
        import anthropic

        from src.agent import Agent

        config = MagicMock()
        config.default_model = "claude-sonnet"
        config.haiku_model = "claude-haiku"
        config.opus_model = "claude-opus"
        config.anthropic_api_key = "test-key"
        config.max_autonomous_turns = 5
        config.session_timeout_hours = 24
        config.max_context_tokens = 120_000

        agent = Agent(
            config=config,
            guardrails=MagicMock(),
            memory=MagicMock(build_context=MagicMock(return_value=""), append_turn=MagicMock()),
            tool_registry={},
            audit=MagicMock(log_tier2=AsyncMock(), log_tool_call=AsyncMock()),
            cost_guard=MagicMock(
                daily_cost_usd=0.0,
                monthly_cost_usd=0.0,
                session_tokens=0,
                record_llm_call=MagicMock(),
                record_autonomous_turn=MagicMock(),
                on_user_message=MagicMock(),
                reset_session=MagicMock(),
            ),
            loop_detector=MagicMock(set_session=MagicMock(), on_user_message=MagicMock()),
            rate_limiter=MagicMock(reset=MagicMock()),
        )

        agent._client.messages.create = AsyncMock(side_effect=anthropic.APIConnectionError(request=MagicMock()))

        result = await agent.run_turn("test")
        assert "trouble" in result.lower() or "try again" in result.lower()

    @pytest.mark.asyncio
    async def test_sub_agent_retries_on_api_error(self) -> None:
        """SubAgentRunner should also retry on transient API errors."""
        import anthropic

        from src.sub_agent import SubAgentRunner

        config = MagicMock()
        config.anthropic_api_key = "test-key"

        runner = SubAgentRunner(config=config, tools={}, model="test-model")

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [MagicMock(text="Done", type="text")]
        mock_response.content[0].text = "Done"
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        # Make content items pass isinstance checks
        from anthropic.types import TextBlock

        text_block = TextBlock(text="Done", type="text")
        mock_response.content = [text_block]

        runner._client.messages.create = AsyncMock(
            side_effect=[
                anthropic.APIConnectionError(request=MagicMock()),
                mock_response,
            ]
        )

        result, tokens = await runner.run("test task")
        assert "Done" in result
        assert runner._client.messages.create.call_count == 2


# ---------------------------------------------------------------------------
# 4. Graceful shutdown — pending task cancellation
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    """JobRegistry should support cancelling all pending tasks."""

    def test_job_registry_has_cancel_all(self) -> None:
        from src.jobs import JobRegistry

        assert hasattr(JobRegistry, "cancel_all")

    @pytest.mark.asyncio
    async def test_cancel_all_cancels_running_tasks(self) -> None:
        from src.jobs import JobRegistry

        registry = JobRegistry()

        async def long_task() -> None:
            await asyncio.sleep(3600)

        task = asyncio.create_task(long_task())
        registry.start("job1", job_type="test", description="test")
        registry.set_task("job1", task)

        cancelled = registry.cancel_all()
        assert cancelled == 1
        # Let the event loop process the cancellation
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_cancel_all_skips_finished_tasks(self) -> None:
        from src.jobs import JobRegistry

        registry = JobRegistry()

        async def quick_task() -> None:
            pass

        task = asyncio.create_task(quick_task())
        await task  # let it finish

        registry.start("job1", job_type="test", description="test")
        registry.set_task("job1", task)

        cancelled = registry.cancel_all()
        assert cancelled == 0
