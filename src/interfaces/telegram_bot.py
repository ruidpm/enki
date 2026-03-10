"""Telegram bot interface — python-telegram-bot async."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import uuid
from typing import Any

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

log = structlog.get_logger()

_CONFIRM_TIMEOUT = 300  # seconds to wait for user response


class TelegramBot:
    """Telegram bot — also implements EvolveNotifier, RestartNotifier, and ConfirmationGate protocols."""

    def __init__(self, token: str, allowed_chat_id: str) -> None:
        self._allowed_chat_id = int(allowed_chat_id)
        self._token = token
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._pending_question: asyncio.Future[str] | None = None
        self._confirm_timeout: float = _CONFIRM_TIMEOUT
        self._agent: Any = None
        self._post_init_cb: Any = None
        self._post_shutdown_cb: Any = None
        self._turn_lock = asyncio.Lock()
        self._whisper_model: Any = None  # lazy-loaded on first voice message
        # App built lazily in run() so post_init/post_shutdown can be set first
        self._app = Application.builder().token(token).build()
        self._register_handlers()

    def set_agent(self, agent: object) -> None:
        self._agent = agent

    def _register_handlers(self) -> None:
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("cost", self._cmd_cost))
        self._app.add_handler(CommandHandler("audit", self._cmd_audit))
        self._app.add_handler(CommandHandler("newsession", self._cmd_newsession))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message, block=False)
        )
        self._app.add_handler(
            MessageHandler(filters.VOICE, self._on_voice, block=False)
        )
        self._app.add_handler(
            MessageHandler(filters.PHOTO, self._on_photo, block=False)
        )
        self._app.add_error_handler(self._on_error)  # type: ignore[arg-type]

    def _authorized(self, update: Update) -> bool:
        return (
            update.effective_chat is not None
            and update.effective_chat.id == self._allowed_chat_id
        )

    def _authorized_chat(self, chat_id: int) -> bool:
        return chat_id == self._allowed_chat_id

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        await update.message.reply_text("Enki online.")  # type: ignore[union-attr]

    async def _cmd_cost(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        assert self._agent is not None
        daily = self._agent._cost_guard.daily_cost_usd
        monthly = self._agent._cost_guard.monthly_cost_usd
        tokens = self._agent._cost_guard.session_tokens
        await update.message.reply_text(  # type: ignore[union-attr]
            f"Session tokens: {tokens:,}\nToday: ${daily:.4f}\nThis month: ${monthly:.4f}"
        )

    async def _cmd_newsession(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        assert self._agent is not None
        self._agent.new_session()
        await update.message.reply_text("New session started. Previous context cleared.")  # type: ignore[union-attr]

    async def _cmd_audit(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        assert self._agent is not None
        from src.audit.query import AuditQuery
        q = AuditQuery(self._agent._audit)
        events = q.get_security_events()
        if not events:
            await update.message.reply_text("No security events recorded.")  # type: ignore[union-attr]
            return
        recent = events[-5:]
        lines = [f"[{e['timestamp'][:16]}] {e['event_type']}" for e in recent]
        await update.message.reply_text("\n".join(lines))  # type: ignore[union-attr]

    async def _run_turn_with_typing(
        self, update: Update, content: str | list[dict[str, Any]]
    ) -> None:
        """Run a turn with persistent typing indicator. update.message must be set."""
        if self._turn_lock.locked():
            await update.message.reply_text("Still processing your last request...")  # type: ignore[union-attr]
            return

        async with self._turn_lock:
            stop_typing = asyncio.Event()

            async def _keep_typing() -> None:
                while not stop_typing.is_set():
                    try:
                        await update.message.chat.send_action("typing")  # type: ignore[union-attr]
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(stop_typing.wait(), timeout=4.0)
                    except asyncio.TimeoutError:
                        pass

            typing_task = asyncio.create_task(_keep_typing())
            try:
                response = await self._agent.run_turn(content)
                await update.message.reply_text(response)  # type: ignore[union-attr]
            except Exception as exc:
                log.error("telegram_error", error=str(exc))
                await update.message.reply_text(f"Error: {exc}")  # type: ignore[union-attr]
            finally:
                stop_typing.set()
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass

    async def _on_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update) or not update.message or not update.message.text:
            return
        assert self._agent is not None

        # If a pipeline stage is waiting for a free-text answer, resolve it
        if self._pending_question is not None and not self._pending_question.done():
            self._pending_question.set_result(update.message.text)
            self._pending_question = None
            return

        await self._run_turn_with_typing(update, update.message.text)

    async def _on_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update) or not update.message or not update.message.voice:
            return
        assert self._agent is not None

        tmp_path = f"/tmp/voice_{uuid.uuid4().hex}.ogg"
        try:
            tg_file = await ctx.bot.get_file(update.message.voice.file_id)
            await tg_file.download_to_drive(tmp_path)

            # Lazy-load local Whisper model on first voice message (tiny = 39MB, ~200ms on CPU)
            if self._whisper_model is None:
                import whisper  # type: ignore[import-untyped]
                self._whisper_model = await asyncio.to_thread(whisper.load_model, "tiny")

            result: dict[str, Any] = await asyncio.to_thread(
                self._whisper_model.transcribe, tmp_path
            )
            transcript = result["text"].strip()
            if not transcript:
                await update.message.reply_text("Couldn't transcribe — empty audio?")
                return
            log.info("voice_transcribed", length=len(transcript))
            await self._run_turn_with_typing(update, transcript)
        except Exception as exc:
            log.error("voice_error", error=str(exc))
            await update.message.reply_text(f"Voice error: {exc}")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def _on_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update) or not update.message or not update.message.photo:
            return
        assert self._agent is not None

        tmp_path = f"/tmp/photo_{uuid.uuid4().hex}.jpg"
        try:
            # Highest resolution is last in the list
            photo = update.message.photo[-1]
            tg_file = await ctx.bot.get_file(photo.file_id)
            await tg_file.download_to_drive(tmp_path)

            with open(tmp_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()

            caption = update.message.caption or "What's in this image?"
            content: list[dict[str, Any]] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                },
                {"type": "text", "text": caption},
            ]
            log.info("photo_received", size=photo.file_size)
            await self._run_turn_with_typing(update, content)
        except Exception as exc:
            log.error("photo_error", error=str(exc))
            await update.message.reply_text(f"Photo error: {exc}")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # ------------------------------------------------------------------
    # Inline keyboard callback handler
    # ------------------------------------------------------------------

    async def _on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()

        if not self._authorized_chat(query.message.chat_id):
            return

        data = query.data or ""
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[0] != "confirm":
            return

        key = parts[1]
        approved = parts[2] == "yes"

        if key in self._pending:
            fut = self._pending.pop(key)
            if not fut.done():
                fut.set_result(approved)

        await query.edit_message_reply_markup(None)

    # ------------------------------------------------------------------
    # Internal helper: send keyboard and await response
    # ------------------------------------------------------------------

    async def _ask(self, text: str, key: str) -> bool:
        """Send inline Yes/No keyboard and wait for user response."""
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Yes", callback_data=f"confirm:{key}:yes"),
                InlineKeyboardButton("No", callback_data=f"confirm:{key}:no"),
            ]]
        )
        await self._app.bot.send_message(
            self._allowed_chat_id, text, reply_markup=keyboard
        )
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._pending[key] = fut
        try:
            result = await asyncio.wait_for(fut, timeout=self._confirm_timeout)
            self._pending.pop(key, None)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(key, None)
            log.warning("telegram_confirm_timeout", key=key)
            return False

    # ------------------------------------------------------------------
    # ConfirmationGate protocol
    # ------------------------------------------------------------------

    async def ask_confirm(self, tool_name: str, params: dict) -> bool:  # type: ignore[type-arg]
        params_str = json.dumps(params, indent=2)[:400]
        text = f"Allow tool: {tool_name}\n\n{params_str}"
        return await self._ask(text, f"tool_{tool_name}")

    # ------------------------------------------------------------------
    # EvolveNotifier protocol
    # ------------------------------------------------------------------

    async def send_diff(
        self, tool_name: str, description: str, code: str, code_hash: str
    ) -> None:
        text = (
            f"New tool proposed: {tool_name}\n"
            f"Description: {description}\n"
            f"SHA256: {code_hash[:16]}\n\n"
            f"--- code ---\n{code[:3000]}\n--- end ---"
        )
        await self._app.bot.send_message(self._allowed_chat_id, text)

    async def wait_for_approval(self, tool_name: str) -> bool:
        return await self._ask(f"Approve tool '{tool_name}'?", f"approve_{tool_name}")

    # ------------------------------------------------------------------
    # RestartNotifier protocol
    # ------------------------------------------------------------------

    async def send(self, message: str) -> None:
        await self._app.bot.send_message(self._allowed_chat_id, message)

    async def ask_single_confirm(self, reason: str, changes_summary: str) -> bool:
        text = f"Reason: {reason}\n\n{changes_summary}\n\nProceed?"
        return await self._ask(text, "single_confirm")

    async def ask_double_confirm(self, reason: str, changes_summary: str) -> bool:
        text1 = f"{reason}\n\n{changes_summary[:300]}\n\nConfirm? (1/2)"
        if not await self._ask(text1, "double_confirm_1"):
            return False
        return await self._ask("Are you sure? (2/2)", "double_confirm_2")

    async def ask_free_text(self, prompt: str, timeout_s: int = 300) -> str | None:
        """Send a question, capture next free-text reply. Returns None on timeout."""
        await self._app.bot.send_message(self._allowed_chat_id, prompt)
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_question: asyncio.Future[str] | None = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            self._pending_question = None
            log.warning("telegram_free_text_timeout")
            return None

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        import httpx
        if isinstance(context.error, Conflict):
            log.warning("telegram_conflict", detail=str(context.error))
            return
        if isinstance(context.error, httpx.ConnectError | httpx.TimeoutException | httpx.NetworkError):
            # Transient network errors — PTB retries automatically
            log.warning("telegram_network_error", error=str(context.error))
            return
        log.error("telegram_unhandled_error", error=str(context.error))

    def set_post_init(self, cb: Any) -> None:
        self._post_init_cb = cb

    def set_post_shutdown(self, cb: Any) -> None:
        self._post_shutdown_cb = cb

    def run(self) -> None:
        log.info("telegram_bot_starting")
        # Rebuild app with lifecycle hooks if provided
        if self._post_init_cb is not None or self._post_shutdown_cb is not None:
            builder = Application.builder().token(self._token)
            if self._post_init_cb is not None:
                builder = builder.post_init(self._post_init_cb)
            if self._post_shutdown_cb is not None:
                builder = builder.post_shutdown(self._post_shutdown_cb)
            self._app = builder.build()
            self._register_handlers()
        self._app.run_polling(allowed_updates=Update.ALL_TYPES)
