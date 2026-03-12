"""Tests for configurable timeouts — all hardcoded constants should read from Settings."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.config import Settings


class TestTimeoutSettings:
    """Settings should expose all timeout fields with sensible defaults."""

    @pytest.fixture
    def settings(self, monkeypatch: pytest.MonkeyPatch) -> Settings:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        return Settings(_env_file=None)  # type: ignore[call-arg]

    def test_restart_cooldown_default(self, settings: Settings) -> None:
        assert settings.restart_cooldown_seconds == 600

    def test_confirm_timeout_default(self, settings: Settings) -> None:
        assert settings.confirm_timeout_seconds == 300

    def test_sub_agent_max_steps_default(self, settings: Settings) -> None:
        assert settings.sub_agent_max_steps == 80

    def test_claude_code_timeout_default(self, settings: Settings) -> None:
        assert settings.claude_code_timeout_seconds == 600

    def test_claude_code_cooldown_default(self, settings: Settings) -> None:
        assert settings.claude_code_cooldown_seconds == 300

    def test_connectivity_timeout_default(self, settings: Settings) -> None:
        assert settings.connectivity_timeout_seconds == 5

    def test_override_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("RESTART_COOLDOWN_SECONDS", "120")
        monkeypatch.setenv("SUB_AGENT_MAX_STEPS", "40")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.restart_cooldown_seconds == 120
        assert s.sub_agent_max_steps == 40


class TestRestartUsesConfig:
    """RequestRestartTool should use config timeout, not hardcoded constant."""

    def test_cooldown_from_config(self) -> None:
        from src.tools.restart import RequestRestartTool

        notifier = MagicMock()
        tool = RequestRestartTool(notifier=notifier, cooldown_seconds=120)
        assert tool._cooldown_seconds == 120


class TestSubAgentUsesConfig:
    """SubAgentRunner should accept max_steps from caller."""

    def test_max_steps_from_init(self) -> None:
        from src.sub_agent import SubAgentRunner

        config = MagicMock()
        config.anthropic_api_key = "k"
        runner = SubAgentRunner(config=config, tools={}, model="m", max_steps=40)
        assert runner._max_steps == 40


class TestTelegramBotUsesConfig:
    """TelegramBot should accept confirm timeout."""

    def test_confirm_timeout_from_init(self) -> None:
        from src.interfaces.telegram_bot import TelegramBot

        bot = TelegramBot(token="t", allowed_chat_id="123", confirm_timeout=120)
        assert bot._confirm_timeout == 120

    def test_confirm_timeout_default(self) -> None:
        from src.interfaces.telegram_bot import TelegramBot

        bot = TelegramBot(token="t", allowed_chat_id="123")
        assert bot._confirm_timeout == 300
