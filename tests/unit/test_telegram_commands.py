"""Tests for Telegram UX commands — /help, /status, /memory."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestHelpCommand:
    """Bot should respond with available tools list on /help."""

    @pytest.mark.asyncio
    async def test_help_lists_tools(self) -> None:
        from src.interfaces.telegram_bot import TelegramBot

        bot = TelegramBot(token="t", allowed_chat_id="123")
        agent = MagicMock()
        agent.tool_names = ["tasks", "web_search", "notes", "calendar_read"]
        bot.set_agent(agent)

        update = MagicMock()
        update.effective_chat.type = "private"
        update.effective_chat.id = 123
        update.message.reply_text = AsyncMock()

        await bot._cmd_help(update, MagicMock())

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "tasks" in text
        assert "web_search" in text

    @pytest.mark.asyncio
    async def test_help_unauthorized(self) -> None:
        from src.interfaces.telegram_bot import TelegramBot

        bot = TelegramBot(token="t", allowed_chat_id="123")
        update = MagicMock()
        update.effective_chat.type = "private"
        update.effective_chat.id = 999
        update.message.reply_text = AsyncMock()

        await bot._cmd_help(update, MagicMock())
        update.message.reply_text.assert_not_called()


class TestStatusCommand:
    """Bot should show running jobs on /status."""

    @pytest.mark.asyncio
    async def test_status_no_jobs(self) -> None:
        from src.interfaces.telegram_bot import TelegramBot

        bot = TelegramBot(token="t", allowed_chat_id="123")
        bot.set_job_registry(MagicMock(list_running=MagicMock(return_value=[])))

        update = MagicMock()
        update.effective_chat.type = "private"
        update.effective_chat.id = 123
        update.message.reply_text = AsyncMock()

        await bot._cmd_status(update, MagicMock())

        text = update.message.reply_text.call_args[0][0]
        assert "idle" in text.lower() or "no" in text.lower()

    @pytest.mark.asyncio
    async def test_status_with_running_jobs(self) -> None:
        from src.interfaces.telegram_bot import TelegramBot

        bot = TelegramBot(token="t", allowed_chat_id="123")
        bot.set_job_registry(
            MagicMock(
                list_running=MagicMock(
                    return_value=[
                        {
                            "job_id": "abc123",
                            "type": "ccc",
                            "description": "refactor tests",
                            "elapsed_s": 45.2,
                        }
                    ]
                )
            )
        )

        update = MagicMock()
        update.effective_chat.type = "private"
        update.effective_chat.id = 123
        update.message.reply_text = AsyncMock()

        await bot._cmd_status(update, MagicMock())

        text = update.message.reply_text.call_args[0][0]
        assert "abc123" in text
        assert "refactor" in text


class TestMemoryCommand:
    """Bot should search memory facts on /memory."""

    @pytest.mark.asyncio
    async def test_memory_search(self) -> None:
        from src.interfaces.telegram_bot import TelegramBot

        bot = TelegramBot(token="t", allowed_chat_id="123")
        agent = MagicMock()
        agent.memory.search_fts.return_value = [
            {"role": "user", "content": "User prefers TDD"},
            {"role": "assistant", "content": "Agent name is Enki"},
        ]
        bot.set_agent(agent)

        update = MagicMock()
        update.effective_chat.type = "private"
        update.effective_chat.id = 123
        update.message.text = "/memory TDD"
        update.message.reply_text = AsyncMock()

        await bot._cmd_memory(update, MagicMock())

        text = update.message.reply_text.call_args[0][0]
        assert "TDD" in text

    @pytest.mark.asyncio
    async def test_memory_no_query_shows_facts_from_file(self, tmp_path: Path) -> None:
        from src.interfaces.telegram_bot import TelegramBot

        bot = TelegramBot(token="t", allowed_chat_id="123")
        agent = MagicMock()
        facts_file = tmp_path / "facts.md"
        facts_file.write_text("- fact 1\n- fact 2\n")
        agent.memory._facts_path = facts_file
        bot.set_agent(agent)

        update = MagicMock()
        update.effective_chat.type = "private"
        update.effective_chat.id = 123
        update.message.text = "/memory"
        update.message.reply_text = AsyncMock()

        await bot._cmd_memory(update, MagicMock())

        text = update.message.reply_text.call_args[0][0]
        assert "fact 1" in text
        assert "fact 2" in text

    @pytest.mark.asyncio
    async def test_memory_no_query_falls_back_to_sqlite(self) -> None:
        from src.interfaces.telegram_bot import TelegramBot

        bot = TelegramBot(token="t", allowed_chat_id="123")
        agent = MagicMock()
        agent.memory._facts_path = None
        agent.memory.get_facts.return_value = ["sqlite fact"]
        bot.set_agent(agent)

        update = MagicMock()
        update.effective_chat.type = "private"
        update.effective_chat.id = 123
        update.message.text = "/memory"
        update.message.reply_text = AsyncMock()

        await bot._cmd_memory(update, MagicMock())

        text = update.message.reply_text.call_args[0][0]
        assert "sqlite fact" in text

    @pytest.mark.asyncio
    async def test_memory_no_results(self) -> None:
        from src.interfaces.telegram_bot import TelegramBot

        bot = TelegramBot(token="t", allowed_chat_id="123")
        agent = MagicMock()
        agent.memory.search_fts.return_value = []
        bot.set_agent(agent)

        update = MagicMock()
        update.effective_chat.type = "private"
        update.effective_chat.id = 123
        update.message.text = "/memory nonexistent"
        update.message.reply_text = AsyncMock()

        await bot._cmd_memory(update, MagicMock())

        text = update.message.reply_text.call_args[0][0]
        assert "No memory" in text
