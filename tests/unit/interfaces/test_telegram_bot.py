"""Tests for TelegramBot — handlers, notifier protocols, confirmation flow."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Message

from src.interfaces.telegram_bot import TelegramBot

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_app() -> MagicMock:
    app = MagicMock()
    app.bot = AsyncMock()
    app.add_handler = MagicMock()
    app.run_polling = MagicMock()
    return app


@pytest.fixture
def bot(mock_app: MagicMock) -> TelegramBot:
    with patch("src.interfaces.telegram_bot.Application") as mock_builder_cls:
        mock_builder_cls.builder.return_value.token.return_value.build.return_value = mock_app
        b = TelegramBot(token="tok", allowed_chat_id="12345")
    # inject mock_app for direct bot access
    b._app = mock_app
    return b


@pytest.fixture
def agent() -> MagicMock:
    a = MagicMock()
    a.run_turn = AsyncMock(return_value="Hello back")
    a.daily_cost_usd = 0.01
    a.monthly_cost_usd = 0.05
    a.session_tokens = 500
    a.audit = MagicMock()
    a.session_id = "sess123"
    return a


def _make_update(chat_id: int = 12345, text: str = "hi", chat_type: str = "private") -> MagicMock:
    update = MagicMock()
    update.effective_chat = MagicMock(id=chat_id, type=chat_type)
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.chat = MagicMock()
    update.message.chat.send_action = AsyncMock()
    return update


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_authorized_correct_chat(bot: TelegramBot) -> None:
    update = _make_update(chat_id=12345)
    assert bot._authorized(update) is True


def test_authorized_wrong_chat(bot: TelegramBot) -> None:
    update = _make_update(chat_id=99999)
    assert bot._authorized(update) is False


# ---------------------------------------------------------------------------
# /start command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_start_authorized(bot: TelegramBot) -> None:
    update = _make_update(chat_id=12345)
    await bot._cmd_start(update, MagicMock())
    update.message.reply_text.assert_awaited_once()
    assert "online" in update.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_cmd_start_unauthorized(bot: TelegramBot) -> None:
    update = _make_update(chat_id=99999)
    await bot._cmd_start(update, MagicMock())
    update.message.reply_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# /cost command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_cost(bot: TelegramBot, agent: MagicMock) -> None:
    bot.set_agent(agent)
    update = _make_update(chat_id=12345)
    await bot._cmd_cost(update, MagicMock())
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "500" in text  # session_tokens


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_calls_agent(bot: TelegramBot, agent: MagicMock) -> None:
    bot.set_agent(agent)
    update = _make_update(chat_id=12345, text="What tasks do I have?")
    await bot._on_message(update, MagicMock())
    agent.run_turn.assert_awaited_once_with("What tasks do I have?")
    update.message.reply_text.assert_awaited_once_with("Hello back")


@pytest.mark.asyncio
async def test_on_message_unauthorized_ignored(bot: TelegramBot, agent: MagicMock) -> None:
    bot.set_agent(agent)
    update = _make_update(chat_id=99999, text="hack")
    await bot._on_message(update, MagicMock())
    agent.run_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_error_sends_error_reply(bot: TelegramBot, agent: MagicMock) -> None:
    bot.set_agent(agent)
    agent.run_turn = AsyncMock(side_effect=RuntimeError("boom"))
    update = _make_update(chat_id=12345, text="oops")
    await bot._on_message(update, MagicMock())
    text = update.message.reply_text.call_args[0][0]
    assert "Error" in text or "error" in text.lower()


# ---------------------------------------------------------------------------
# Callback query handler
# ---------------------------------------------------------------------------


def _make_callback_update(data: str, chat_id: int = 12345, from_user_id: int | None = None) -> MagicMock:
    update = MagicMock()
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock(spec=Message)
    query.message.chat_id = chat_id
    # from_user defaults to chat_id if not specified
    query.from_user = MagicMock()
    query.from_user.id = from_user_id if from_user_id is not None else chat_id
    update.callback_query = query
    return update


@pytest.mark.asyncio
async def test_callback_resolves_pending_future_yes(bot: TelegramBot) -> None:
    loop = asyncio.get_event_loop()
    fut: asyncio.Future[bool] = loop.create_future()
    bot._pending["my_key"] = fut

    update = _make_callback_update("confirm:my_key:yes")
    await bot._on_callback(update, MagicMock())

    assert fut.done()
    assert fut.result() is True
    assert "my_key" not in bot._pending


@pytest.mark.asyncio
async def test_callback_resolves_pending_future_no(bot: TelegramBot) -> None:
    loop = asyncio.get_event_loop()
    fut: asyncio.Future[bool] = loop.create_future()
    bot._pending["my_key"] = fut

    update = _make_callback_update("confirm:my_key:no")
    await bot._on_callback(update, MagicMock())

    assert fut.done()
    assert fut.result() is False


# ---------------------------------------------------------------------------
# H-02: Verify Telegram callback sender ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_wrong_from_user_ignored(bot: TelegramBot) -> None:
    """Callback from a different user (but same chat) must be rejected."""
    loop = asyncio.get_event_loop()
    fut: asyncio.Future[bool] = loop.create_future()
    bot._pending["my_key"] = fut

    update = _make_callback_update("confirm:my_key:yes", chat_id=12345, from_user_id=99999)
    await bot._on_callback(update, MagicMock())

    assert not fut.done()  # future must NOT be resolved


@pytest.mark.asyncio
async def test_callback_correct_from_user_resolves(bot: TelegramBot) -> None:
    """Callback from the authorized user should resolve the future."""
    loop = asyncio.get_event_loop()
    fut: asyncio.Future[bool] = loop.create_future()
    bot._pending["my_key"] = fut

    update = _make_callback_update("confirm:my_key:yes", chat_id=12345, from_user_id=12345)
    await bot._on_callback(update, MagicMock())

    assert fut.done()
    assert fut.result() is True


@pytest.mark.asyncio
async def test_callback_unknown_key_does_not_raise(bot: TelegramBot) -> None:
    update = _make_callback_update("confirm:unknown_key:yes")
    await bot._on_callback(update, MagicMock())  # should not raise


@pytest.mark.asyncio
async def test_callback_unauthorized_does_not_resolve(bot: TelegramBot) -> None:
    loop = asyncio.get_event_loop()
    fut: asyncio.Future[bool] = loop.create_future()
    bot._pending["my_key"] = fut

    update = _make_callback_update("confirm:my_key:yes", chat_id=99999)
    await bot._on_callback(update, MagicMock())

    assert not fut.done()  # future untouched


# ---------------------------------------------------------------------------
# Notifier protocol methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_sends_message(bot: TelegramBot) -> None:
    await bot.send("Restarting now")
    bot._app.bot.send_message.assert_awaited_once_with(12345, "Restarting now")


@pytest.mark.asyncio
async def test_send_diff_sends_formatted_message(bot: TelegramBot) -> None:
    await bot.send_diff("my_tool", "does X", "code here", "abc123def456")
    bot._app.bot.send_message.assert_awaited_once()
    call_text = bot._app.bot.send_message.call_args[0][1]
    assert "my_tool" in call_text
    assert "abc123de" in call_text  # first 8 chars of hash


@pytest.mark.asyncio
async def test_ask_confirm_sends_keyboard_and_returns_on_yes(bot: TelegramBot) -> None:
    """_ask() resolves when the pending future is set to True."""
    bot._confirm_timeout = 5.0

    async def _resolve() -> bool:
        # Wait until _ask has registered the future, then resolve it
        for _ in range(50):
            await asyncio.sleep(0.01)
            if bot._pending:
                key = next(iter(bot._pending))
                fut = bot._pending[key]
                if not fut.done():
                    fut.set_result(True)
                return True
        return False

    task = asyncio.create_task(_resolve())
    result = await bot.ask_confirm("git_commit", {"message": "fix bug"})
    await task
    assert result is True
    bot._app.bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_ask_confirm_timeout_returns_false(bot: TelegramBot) -> None:
    bot._confirm_timeout = 0.05
    result = await bot.ask_confirm("git_push", {})
    assert result is False


@pytest.mark.asyncio
async def test_wait_for_approval_returns_true_on_yes(bot: TelegramBot) -> None:
    bot._confirm_timeout = 5.0

    async def _resolve() -> None:
        for _ in range(50):
            await asyncio.sleep(0.01)
            if bot._pending:
                key = next(iter(bot._pending))
                fut = bot._pending[key]
                if not fut.done():
                    fut.set_result(True)
                return

    asyncio.create_task(_resolve())
    result = await bot.wait_for_approval("my_tool")
    assert result is True


@pytest.mark.asyncio
async def test_ask_double_confirm_both_yes(bot: TelegramBot) -> None:
    bot._confirm_timeout = 5.0
    resolved: list[str] = []

    async def _resolve_all() -> None:
        while len(resolved) < 2:
            await asyncio.sleep(0.01)
            if bot._pending:
                key = next(iter(bot._pending))
                fut = bot._pending[key]
                if not fut.done():
                    fut.set_result(True)
                    resolved.append(key)

    asyncio.create_task(_resolve_all())
    result = await bot.ask_double_confirm("apply changes", "updated agent.py")
    assert result is True
    assert bot._app.bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_ask_double_confirm_first_no_skips_second(bot: TelegramBot) -> None:
    bot._confirm_timeout = 5.0

    async def _resolve_no() -> None:
        for _ in range(50):
            await asyncio.sleep(0.01)
            if bot._pending:
                key = next(iter(bot._pending))
                fut = bot._pending[key]
                if not fut.done():
                    fut.set_result(False)
                return

    asyncio.create_task(_resolve_no())
    result = await bot.ask_double_confirm("apply changes", "updated agent.py")
    assert result is False
    # Only 1 send_message call (second confirmation never reached)
    assert bot._app.bot.send_message.await_count == 1


# ---------------------------------------------------------------------------
# M-04: Secure temp file creation
# ---------------------------------------------------------------------------


def test_voice_handler_uses_tempfile_mkstemp() -> None:
    """Verify the module uses tempfile for secure temp files, not f-string paths."""
    import inspect

    import src.interfaces.telegram_bot as mod

    source = inspect.getsource(mod.TelegramBot._on_voice)
    # Must use tempfile.mkstemp, not f-string /tmp/ paths
    assert "mkstemp" in source or "NamedTemporaryFile" in source
    assert 'f"/tmp/' not in source


def test_photo_handler_uses_tempfile_mkstemp() -> None:
    """Verify the module uses tempfile for secure temp files, not f-string paths."""
    import inspect

    import src.interfaces.telegram_bot as mod

    source = inspect.getsource(mod.TelegramBot._on_photo)
    assert "mkstemp" in source or "NamedTemporaryFile" in source
    assert 'f"/tmp/' not in source
