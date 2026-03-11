"""Shared fixtures for all tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.audit.db import AuditDB
from src.config import Settings


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        anthropic_api_key="test-key",
        telegram_bot_token="test-token",
        brave_search_api_key="test-brave",
        telegram_chat_id="123456",
    )


@pytest.fixture
def audit_db(tmp_path: Path) -> AuditDB:
    return AuditDB(tmp_path / "audit.db")


@pytest.fixture
def mock_audit_writer() -> AsyncMock:
    writer = AsyncMock()
    writer.log_tool_call = AsyncMock(return_value=None)
    return writer


@pytest.fixture
def mock_notifier() -> AsyncMock:
    notifier = AsyncMock()
    notifier.ask_confirm = AsyncMock(return_value=True)
    return notifier
