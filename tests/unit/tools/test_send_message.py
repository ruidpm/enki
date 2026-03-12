"""Unit tests for SendMessageTool."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.tools.send_message import SendMessageTool


@pytest.mark.asyncio
async def test_execute_calls_notifier_send() -> None:
    notifier = AsyncMock()
    tool = SendMessageTool(notifier=notifier)
    result = await tool.execute(message="On it.")
    notifier.send.assert_called_once_with("On it.")
    assert result == "Message sent."


@pytest.mark.asyncio
async def test_execute_returns_sent_confirmation() -> None:
    notifier = AsyncMock()
    tool = SendMessageTool(notifier=notifier)
    result = await tool.execute(message="Searching now.")
    assert "sent" in result.lower()
