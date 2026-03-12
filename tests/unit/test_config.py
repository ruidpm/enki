"""Tests for application configuration (src/config.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import Settings


class TestSettingsDefaults:
    """Default values should be sensible and valid."""

    def test_default_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert "sonnet" in s.default_model
        assert "haiku" in s.haiku_model
        assert "opus" in s.opus_model

    def test_default_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.audit_db_path == Path("data/audit.db")
        assert s.memory_db_path == Path("data/memory.db")
        assert s.tasks_db_path == Path("data/tasks.db")

    def test_default_cost_limits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.max_daily_cost_usd == 50.0
        assert s.max_monthly_cost_usd == 300.0
        assert s.max_tokens_per_session == 5_000_000


class TestSettingsEnvOverride:
    """Environment variables should override defaults."""

    def test_override_cost_limits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("MAX_DAILY_COST_USD", "100.0")
        monkeypatch.setenv("MAX_MONTHLY_COST_USD", "500.0")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.max_daily_cost_usd == 100.0
        assert s.max_monthly_cost_usd == 500.0

    def test_override_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("DEFAULT_MODEL", "custom-model")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.default_model == "custom-model"

    def test_optional_email_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.imap_host is None
        assert s.imap_user is None
        assert s.imap_password is None

    def test_email_fields_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("IMAP_HOST", "imap.example.com")
        monkeypatch.setenv("IMAP_USER", "user@example.com")
        monkeypatch.setenv("IMAP_PASSWORD", "secret")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.imap_host == "imap.example.com"
        assert s.imap_user == "user@example.com"


class TestSettingsValidation:
    """Pydantic validation should enforce constraints."""

    def test_missing_required_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_negative_cost_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("MAX_DAILY_COST_USD", "-10")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_zero_session_tokens_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("MAX_TOKENS_PER_SESSION", "0")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_extra_env_vars_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "b")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COMPLETELY_UNKNOWN_VAR", "whatever")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.anthropic_api_key == "k"
