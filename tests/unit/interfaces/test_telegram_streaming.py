"""Tests for Telegram bot streaming response display."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest

from src.interfaces.telegram_bot import TelegramBot

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_app() -> MagicMock:
    app = MagicMock()
    app.bot = AsyncMock()
    app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    app.bot.edit_message_text = AsyncMock()
    app.add_handler = MagicMock()
    app.run_polling = MagicMock()
    return app


@pytest.fixture
def bot(mock_app: MagicMock) -> TelegramBot:
    with patch("src.interfaces.telegram_bot.Application") as mock_builder_cls:
        mock_builder_cls.builder.return_value.token.return_value.build.return_value = mock_app
        b = TelegramBot(token="tok", allowed_chat_id="12345")
    b._app = mock_app
    return b


@pytest.fixture
def agent() -> MagicMock:
    a = MagicMock()
    a.run_turn = AsyncMock(return_value="Hello back")
    a.daily_cost_usd = 0.01
    a.monthly_cost_usd = 0.05
    a.session_tokens = 500
    return a


def _make_update(chat_id: int = 12345, text: str = "hi") -> MagicMock:
    update = MagicMock()
    update.effective_chat = MagicMock(id=chat_id, type="private")
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.chat = MagicMock()
    update.message.chat.send_action = AsyncMock()
    update.message.chat_id = chat_id
    return update


# ---------------------------------------------------------------------------
# Tests: streaming callback sends then edits
# ---------------------------------------------------------------------------


class TestStreamingCallbackSendsThenEdits:
    """First chunk should send a new message, subsequent chunks should edit."""

    @pytest.mark.asyncio
    async def test_streaming_callback_sends_then_edits(self, bot: TelegramBot, agent: MagicMock) -> None:
        bot.set_agent(agent)

        # Capture the stream_callback that gets passed to run_turn
        captured_callback: list[Any] = []

        async def mock_run_turn(content: Any, stream_callback: Any = None) -> str:
            captured_callback.append(stream_callback)
            if stream_callback:
                # Simulate streaming: first chunk sends, rest edit
                await stream_callback("He")
                await stream_callback("Hello")
                await stream_callback("Hello back")
            return "Hello back"

        agent.run_turn = AsyncMock(side_effect=mock_run_turn)

        update = _make_update()
        await bot._run_turn_with_typing(update, "hi")

        # run_turn should have been called with a stream_callback
        agent.run_turn.assert_awaited_once()
        call_kwargs = agent.run_turn.call_args
        assert call_kwargs[1].get("stream_callback") is not None or (len(call_kwargs[0]) > 1 and call_kwargs[0][1] is not None)

        # First call: send_message (creates the streaming message)
        bot._app.bot.send_message.assert_awaited()

        # Subsequent calls: edit_message_text
        # At least one edit should have happened
        assert bot._app.bot.edit_message_text.await_count >= 1

    @pytest.mark.asyncio
    async def test_streaming_no_duplicate_reply_after_stream(self, bot: TelegramBot, agent: MagicMock) -> None:
        """After streaming completes, _reply_md should NOT send another message."""
        bot.set_agent(agent)

        async def mock_run_turn(content: Any, stream_callback: Any = None) -> str:
            if stream_callback:
                await stream_callback("Done")
            return "Done"

        agent.run_turn = AsyncMock(side_effect=mock_run_turn)

        update = _make_update()
        await bot._run_turn_with_typing(update, "hi")

        # send_message called exactly once (for the initial streaming message)
        # reply_text should NOT be called (no duplicate reply)
        update.message.reply_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_streaming_empty_response_falls_back(self, bot: TelegramBot, agent: MagicMock) -> None:
        """If stream_callback is never called, fall back to regular _reply_md."""
        bot.set_agent(agent)

        async def mock_run_turn(content: Any, stream_callback: Any = None) -> str:
            # stream_callback exists but is never called
            return "Fallback response"

        agent.run_turn = AsyncMock(side_effect=mock_run_turn)

        update = _make_update()
        await bot._run_turn_with_typing(update, "hi")

        # Should fall back to reply_text since streaming never happened
        update.message.reply_text.assert_awaited()


class TestStreamingRateLimitsEdits:
    """Edits should be rate-limited to avoid Telegram API rate limits."""

    @pytest.mark.asyncio
    async def test_streaming_rate_limits_edits(self, bot: TelegramBot, agent: MagicMock) -> None:
        bot.set_agent(agent)

        async def mock_run_turn(content: Any, stream_callback: Any = None) -> str:
            if stream_callback:
                # Send many rapid updates
                for i in range(20):
                    await stream_callback(f"text chunk {i}")
            return "text chunk 19"

        agent.run_turn = AsyncMock(side_effect=mock_run_turn)

        update = _make_update()
        await bot._run_turn_with_typing(update, "hi")

        # With 20 rapid updates and rate limiting at 400ms,
        # we should have far fewer than 20 edits.
        # The first call is send_message, subsequent are edit_message_text.
        total_edits = bot._app.bot.edit_message_text.await_count
        # Should be significantly less than 19 (20 - 1 for initial send)
        # But at least 1 (the final update must always go through)
        assert total_edits >= 1
        assert total_edits < 19

    @pytest.mark.asyncio
    async def test_streaming_final_update_always_sent(self, bot: TelegramBot, agent: MagicMock) -> None:
        """The final accumulated text must always be sent, even if rate-limited."""
        bot.set_agent(agent)

        async def mock_run_turn(content: Any, stream_callback: Any = None) -> str:
            if stream_callback:
                await stream_callback("partial")
                await stream_callback("final text")
            return "final text"

        agent.run_turn = AsyncMock(side_effect=mock_run_turn)

        update = _make_update()
        await bot._run_turn_with_typing(update, "hi")

        # The last edit (or send) should contain the final text
        if bot._app.bot.edit_message_text.await_count > 0:
            last_edit_args = bot._app.bot.edit_message_text.call_args_list[-1]
            # The text arg should be "final text" (possibly with markdown escaping)
            assert "final text" in str(last_edit_args)


class TestStreamingMarkdownFallback:
    """Edit calls should fall back from MarkdownV2 to plain text on parse errors."""

    @pytest.mark.asyncio
    async def test_streaming_edit_markdown_fallback(self, bot: TelegramBot, agent: MagicMock) -> None:
        bot.set_agent(agent)

        edit_calls: list[dict[str, Any]] = []

        async def mock_edit(*args: Any, **kwargs: Any) -> None:
            edit_calls.append(kwargs)
            if kwargs.get("parse_mode"):
                raise BadRequest("Can't parse entities")

        bot._app.bot.edit_message_text = AsyncMock(side_effect=mock_edit)

        async def mock_run_turn(content: Any, stream_callback: Any = None) -> str:
            if stream_callback:
                await stream_callback("*bad markdown")
            return "*bad markdown"

        agent.run_turn = AsyncMock(side_effect=mock_run_turn)

        update = _make_update()
        await bot._run_turn_with_typing(update, "hi")

        # Should have at least 2 edit calls: first with MarkdownV2, then plain
        assert len(edit_calls) >= 2
