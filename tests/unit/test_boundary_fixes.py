"""Tests for architecture boundary fixes.

Covers:
1. Canonical Notifier protocol has all required methods
2. Agent exposes public properties (no private access needed)
3. AgentProtocol exists and is runtime_checkable
4. REQUIRES_CONFIRM lives in constants, not guardrails
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock


class TestNotifierProtocolConsolidation:
    """Verify the canonical Notifier protocol has all methods used across the codebase."""

    def test_notifier_has_send(self) -> None:
        from src.interfaces.notifier import Notifier

        assert hasattr(Notifier, "send")

    def test_notifier_has_ask_confirm(self) -> None:
        from src.interfaces.notifier import Notifier

        assert hasattr(Notifier, "ask_confirm")

    def test_notifier_has_ask_single_confirm(self) -> None:
        from src.interfaces.notifier import Notifier

        assert hasattr(Notifier, "ask_single_confirm")

    def test_notifier_has_ask_double_confirm(self) -> None:
        from src.interfaces.notifier import Notifier

        assert hasattr(Notifier, "ask_double_confirm")

    def test_notifier_has_ask_free_text(self) -> None:
        from src.interfaces.notifier import Notifier

        assert hasattr(Notifier, "ask_free_text")

    def test_notifier_has_send_diff(self) -> None:
        from src.interfaces.notifier import Notifier

        assert hasattr(Notifier, "send_diff")

    def test_notifier_has_wait_for_approval(self) -> None:
        from src.interfaces.notifier import Notifier

        assert hasattr(Notifier, "wait_for_approval")

    def test_notifier_is_runtime_checkable(self) -> None:
        from src.interfaces.notifier import Notifier

        mock = MagicMock()
        mock.send = AsyncMock()
        mock.ask_confirm = AsyncMock()
        mock.ask_single_confirm = AsyncMock()
        mock.ask_double_confirm = AsyncMock()
        mock.ask_free_text = AsyncMock()
        mock.ask_scope_approval = AsyncMock()
        mock.send_diff = AsyncMock()
        mock.wait_for_approval = AsyncMock()
        assert isinstance(mock, Notifier)

    def test_no_local_notifier_in_confirmation_gate(self) -> None:
        """confirmation_gate should import Notifier from interfaces, not define its own."""
        import src.guardrails.confirmation_gate as mod
        from src.interfaces.notifier import Notifier

        assert mod.Notifier is Notifier  # type: ignore[attr-defined]

    def test_no_local_notifier_in_spawn_team(self) -> None:
        """spawn_team should import Notifier from interfaces."""
        import src.tools.spawn_team as mod
        from src.interfaces.notifier import Notifier

        assert not hasattr(mod, "Notifier") or mod.Notifier is Notifier  # type: ignore[attr-defined]

    def test_no_local_notifier_in_run_pipeline(self) -> None:
        """run_pipeline should import Notifier from interfaces."""
        import src.tools.run_pipeline as mod
        from src.interfaces.notifier import Notifier

        assert not hasattr(mod, "Notifier") or mod.Notifier is Notifier  # type: ignore[attr-defined]

    def test_no_local_notifier_in_claude_code(self) -> None:
        """claude_code should import Notifier from interfaces."""
        import src.tools.claude_code as mod  # noqa: F841

        assert not hasattr(mod, "ClaudeCodeNotifier")


class TestAgentPublicProperties:
    """Agent should expose cost/audit info via public properties."""

    def test_agent_has_daily_cost_property(self) -> None:
        from src.agent import Agent

        assert hasattr(Agent, "daily_cost_usd")
        assert isinstance(Agent.daily_cost_usd, property)

    def test_agent_has_monthly_cost_property(self) -> None:
        from src.agent import Agent

        assert hasattr(Agent, "monthly_cost_usd")
        assert isinstance(Agent.monthly_cost_usd, property)

    def test_agent_has_session_tokens_property(self) -> None:
        from src.agent import Agent

        assert hasattr(Agent, "session_tokens")
        assert isinstance(Agent.session_tokens, property)

    def test_agent_has_audit_property(self) -> None:
        from src.agent import Agent

        assert hasattr(Agent, "audit")
        assert isinstance(Agent.audit, property)

    def test_telegram_bot_uses_public_properties(self) -> None:
        """TelegramBot._cmd_cost should NOT access _cost_guard directly."""
        from src.interfaces.telegram_bot import TelegramBot

        source = inspect.getsource(TelegramBot._cmd_cost)
        assert "_cost_guard" not in source
        assert "daily_cost_usd" in source
        assert "monthly_cost_usd" in source
        assert "session_tokens" in source

    def test_telegram_bot_audit_uses_public_property(self) -> None:
        """TelegramBot._cmd_audit should NOT access _audit directly."""
        from src.interfaces.telegram_bot import TelegramBot

        source = inspect.getsource(TelegramBot._cmd_audit)
        assert "._audit" not in source
        assert ".audit" in source


class TestAgentProtocol:
    """AgentProtocol should exist and be importable."""

    def test_agent_protocol_exists(self) -> None:
        from src.interfaces.agent_protocol import AgentProtocol

        assert hasattr(AgentProtocol, "run_turn")

    def test_agent_protocol_is_runtime_checkable(self) -> None:
        from src.interfaces.agent_protocol import AgentProtocol

        mock = MagicMock()
        mock.run_turn = AsyncMock()
        assert isinstance(mock, AgentProtocol)

    def test_spawn_team_uses_agent_protocol(self) -> None:
        """spawn_team should use AgentProtocol, not its own Agent protocol."""
        import src.tools.spawn_team as mod

        assert not hasattr(mod, "Agent")

    def test_run_pipeline_agent_typed(self) -> None:
        """run_pipeline._agent should not be typed as Any."""
        from src.tools.run_pipeline import RunPipelineTool

        source = inspect.getsource(RunPipelineTool.__init__)
        assert "self._agent: Any" not in source

    def test_claude_code_agent_typed(self) -> None:
        """claude_code._agent should not be typed as Any."""
        from src.tools.claude_code import RunClaudeCodeTool

        source = inspect.getsource(RunClaudeCodeTool.__init__)
        assert "self._agent: Any" not in source


class TestRequiresConfirmInConstants:
    """REQUIRES_CONFIRM should live in src/constants.py, not guardrails."""

    def test_constants_module_exists(self) -> None:
        import src.constants

        assert hasattr(src.constants, "REQUIRES_CONFIRM")

    def test_requires_confirm_is_frozenset(self) -> None:
        from src.constants import REQUIRES_CONFIRM

        assert isinstance(REQUIRES_CONFIRM, frozenset)

    def test_requires_confirm_has_expected_tools(self) -> None:
        from src.constants import REQUIRES_CONFIRM

        assert "git_commit" in REQUIRES_CONFIRM
        assert "manage_team" in REQUIRES_CONFIRM
        assert "request_restart" in REQUIRES_CONFIRM

    def test_confirmation_gate_imports_from_constants(self) -> None:
        """confirmation_gate should import REQUIRES_CONFIRM from constants."""
        from src.constants import REQUIRES_CONFIRM as from_constants
        from src.guardrails.confirmation_gate import REQUIRES_CONFIRM as from_gate

        assert from_gate is from_constants

    def test_spawn_team_imports_from_constants(self) -> None:
        import src.tools.spawn_team as mod

        source = inspect.getsource(mod)
        assert "from src.constants import REQUIRES_CONFIRM" in source

    def test_spawn_agent_imports_from_constants(self) -> None:
        import src.tools.spawn_agent as mod

        source = inspect.getsource(mod)
        assert "from src.constants import REQUIRES_CONFIRM" in source
