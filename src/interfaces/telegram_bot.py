"""Telegram bot interface — python-telegram-bot async."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import tempfile
from typing import Any

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict
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

# MarkdownV2 special chars that must be escaped outside formatting spans.
_MDV2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def escape_mdv2(text: str) -> str:
    """Escape MarkdownV2 special characters in plain text segments."""
    return _MDV2_ESCAPE_RE.sub(r"\\\1", text)


class TelegramBot:
    """Telegram bot — also implements EvolveNotifier, RestartNotifier, and ConfirmationGate protocols."""

    def __init__(self, token: str, allowed_chat_id: str, confirm_timeout: int = _CONFIRM_TIMEOUT) -> None:
        self._allowed_chat_id = int(allowed_chat_id)
        self._token = token
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._pending_str: dict[str, asyncio.Future[str]] = {}
        self._pending_question: asyncio.Future[str] | None = None
        self._confirm_timeout: float = confirm_timeout
        self._agent: Any = None
        self._job_registry: Any = None
        self._audit_query: Any = None
        self._post_init_cb: Any = None
        self._post_shutdown_cb: Any = None
        self._turn_lock = asyncio.Lock()
        self._whisper_model: Any = None  # lazy-loaded on first voice message
        # App built lazily in run() so post_init/post_shutdown can be set first
        self._app = Application.builder().token(token).build()
        self._register_handlers()

    def set_agent(self, agent: object) -> None:
        self._agent = agent

    def set_job_registry(self, registry: object) -> None:
        self._job_registry = registry

    def set_audit_query(self, audit_query: object) -> None:
        self._audit_query = audit_query

    def _register_handlers(self) -> None:
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("cost", self._cmd_cost))
        self._app.add_handler(CommandHandler("audit", self._cmd_audit))
        self._app.add_handler(CommandHandler("newsession", self._cmd_newsession))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("memory", self._cmd_memory))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message, block=False))
        self._app.add_handler(MessageHandler(filters.VOICE, self._on_voice, block=False))
        self._app.add_handler(MessageHandler(filters.PHOTO, self._on_photo, block=False))
        self._app.add_error_handler(self._on_error)

    def _authorized(self, update: Update) -> bool:
        chat = update.effective_chat
        return chat is not None and chat.type == "private" and chat.id == self._allowed_chat_id

    def _authorized_chat(self, chat_id: int) -> bool:
        return chat_id == self._allowed_chat_id

    # ------------------------------------------------------------------
    # MarkdownV2 helpers
    # ------------------------------------------------------------------

    async def _send_md(self, chat_id: int, text: str, **kwargs: Any) -> Message:
        """Send with MarkdownV2, fall back to plain text on parse failure."""
        try:
            return await self._app.bot.send_message(
                chat_id,
                text,
                parse_mode=ParseMode.MARKDOWN_V2,
                **kwargs,
            )
        except BadRequest as exc:
            if "parse" in str(exc).lower() or "can't" in str(exc).lower():
                log.warning("mdv2_parse_fallback", error=str(exc))
                return await self._app.bot.send_message(chat_id, text, **kwargs)
            raise

    async def _reply_md(self, message: Any, text: str) -> Message:
        """reply_text with MarkdownV2, fall back to plain text on parse failure."""
        try:
            return await message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)  # type: ignore[no-any-return]
        except BadRequest as exc:
            if "parse" in str(exc).lower() or "can't" in str(exc).lower():
                log.warning("mdv2_parse_fallback", error=str(exc))
                return await message.reply_text(text)  # type: ignore[no-any-return]
            raise

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
        tokens = self._agent.session_tokens

        if self._audit_query is not None:
            from datetime import UTC, datetime

            now = datetime.now(tz=UTC)
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            daily_data = self._audit_query.get_costs(since=day_start)
            monthly_data = self._audit_query.get_costs(since=month_start)
            daily = daily_data["total_cost_usd"]
            monthly = monthly_data["total_cost_usd"]
        else:
            daily = self._agent.daily_cost_usd
            monthly = self._agent.monthly_cost_usd

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

        q = AuditQuery(self._agent.audit)
        events = q.get_security_events()
        if not events:
            await update.message.reply_text("No security events recorded.")  # type: ignore[union-attr]
            return
        recent = events[-5:]
        lines = [f"[{e['timestamp'][:16]}] {e['event_type']}" for e in recent]
        await update.message.reply_text("\n".join(lines))  # type: ignore[union-attr]

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        assert self._agent is not None
        tools = self._agent.tool_names
        lines = ["Available tools:"] + [f"- {t}" for t in tools]
        lines.append("\nCommands: /start /cost /audit /newsession /help /status /memory")
        await update.message.reply_text("\n".join(lines))  # type: ignore[union-attr]

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        if self._job_registry is None:
            await update.message.reply_text("No job registry configured.")  # type: ignore[union-attr]
            return
        running = self._job_registry.list_running()
        if not running:
            await update.message.reply_text("All idle — no running jobs.")  # type: ignore[union-attr]
            return
        lines = [f"{len(running)} running job(s):\n"]
        for j in running:
            elapsed = int(j.get("elapsed_s", 0))
            mins, secs = divmod(elapsed, 60)
            time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
            stage = f" [{j['stage']}]" if j.get("stage") else ""
            tokens = j.get("tokens_total", 0)
            cost = j.get("cost_usd", 0.0)
            cost_str = f"\n  {tokens:,} tok ~${cost:.4f}" if tokens > 0 else ""
            lines.append(f"- [{j['job_id']}] {j['type']}{stage} {time_str}{cost_str}\n  {j['description'][:80]}")
        await update.message.reply_text("\n".join(lines))  # type: ignore[union-attr]

    async def _cmd_memory(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        assert self._agent is not None
        # Extract query from message text: "/memory <query>" or just "/memory"
        text = (update.message.text or "").strip()  # type: ignore[union-attr]
        query = text[len("/memory") :].strip() if text.startswith("/memory") else ""
        if query:
            results = self._agent.memory.search_fts(query, limit=5)
            if not results:
                await update.message.reply_text(f"No memory matches for: {query}")  # type: ignore[union-attr]
                return
            lines = [f"Memory search: {query}"]
            for r in results:
                content = str(r.get("content", ""))[:200]
                lines.append(f"- [{r.get('role', '?')}] {content}")
            await update.message.reply_text("\n".join(lines))  # type: ignore[union-attr]
        else:
            # Read from facts.md (current source of truth), fall back to SQLite
            facts_text = ""
            if self._agent.memory._facts_path and self._agent.memory._facts_path.exists():
                facts_text = self._agent.memory._facts_path.read_text().strip()
            if facts_text:
                # Truncate for Telegram message limit
                if len(facts_text) > 3500:
                    facts_text = facts_text[:3500] + "\n...[truncated]"
                await update.message.reply_text(f"Known facts:\n{facts_text}")  # type: ignore[union-attr]
            else:
                facts = self._agent.memory.get_facts(limit=10)
                if not facts:
                    await update.message.reply_text("No facts stored yet.")  # type: ignore[union-attr]
                    return
                lines = ["Recent facts:"] + [f"- {f}" for f in facts]
                await update.message.reply_text("\n".join(lines))  # type: ignore[union-attr]

    async def _run_turn_with_typing(self, update: Update, content: str | list[dict[str, Any]]) -> None:
        """Run a turn with persistent typing indicator. update.message must be set."""
        if self._turn_lock.locked():
            await update.message.reply_text("Still processing your last request...")  # type: ignore[union-attr]
            return

        async with self._turn_lock:
            stop_typing = asyncio.Event()

            async def _keep_typing() -> None:
                while not stop_typing.is_set():
                    with contextlib.suppress(Exception):
                        await update.message.chat.send_action("typing")  # type: ignore[union-attr]
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop_typing.wait(), timeout=4.0)

            typing_task = asyncio.create_task(_keep_typing())
            try:
                response = await self._agent.run_turn(content)
                await self._reply_md(update.message, response)
            except Exception as exc:
                log.error("telegram_error", error=str(exc))
                await update.message.reply_text(f"Error: {exc}")  # type: ignore[union-attr]
            finally:
                stop_typing.set()
                typing_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await typing_task

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

        fd, tmp_path = tempfile.mkstemp(suffix=".ogg", prefix="voice_")
        os.close(fd)  # close the fd; download_to_drive writes by path
        try:
            tg_file = await ctx.bot.get_file(update.message.voice.file_id)
            await tg_file.download_to_drive(tmp_path)

            # Lazy-load local Whisper model on first voice message (tiny = 39MB, ~200ms on CPU)
            if self._whisper_model is None:
                import whisper  # type: ignore[import-untyped]

                self._whisper_model = await asyncio.to_thread(whisper.load_model, "tiny")

            result: dict[str, Any] = await asyncio.to_thread(self._whisper_model.transcribe, tmp_path)
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

        fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="photo_")
        os.close(fd)  # close the fd; download_to_drive writes by path
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

        msg = query.message
        if not isinstance(msg, Message) or not self._authorized_chat(msg.chat_id):
            return

        # Verify the callback comes from the authorized user, not a different group member
        if query.from_user is None or query.from_user.id != self._allowed_chat_id:
            log.warning(
                "callback_unauthorized_sender",
                from_user_id=getattr(query.from_user, "id", None),
                expected=self._allowed_chat_id,
            )
            return

        data = query.data or ""
        parts = data.split(":", 2)
        if len(parts) < 2:
            return

        # scope:{approve|reject|revise} — string-valued future
        if parts[0] == "scope" and len(parts) == 2:
            action = parts[1]
            key = "scope_approval"
            if key in self._pending_str:
                fut_str = self._pending_str.pop(key)
                if not fut_str.done():
                    fut_str.set_result(action)
            await query.edit_message_reply_markup(None)
            return

        # confirm:{key}:{yes|no} — bool-valued future
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
            [
                [
                    InlineKeyboardButton("Yes", callback_data=f"confirm:{key}:yes"),
                    InlineKeyboardButton("No", callback_data=f"confirm:{key}:no"),
                ]
            ]
        )
        await self._app.bot.send_message(self._allowed_chat_id, text, reply_markup=keyboard)
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._pending[key] = fut
        try:
            result = await asyncio.wait_for(fut, timeout=self._confirm_timeout)
            self._pending.pop(key, None)
            return result
        except TimeoutError:
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

    async def send_diff(self, tool_name: str, description: str, code: str, code_hash: str) -> None:
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
        await self._send_md(self._allowed_chat_id, message)

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
        self._pending_question = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except TimeoutError:
            self._pending_question = None
            log.warning("telegram_free_text_timeout")
            return None

    async def ask_scope_approval(self, prompt: str, timeout_s: int = 600) -> str | None:
        """3-button scope approval: Approve / Reject / Revise.

        Returns "approve", "reject", free-text feedback (if Revise), or None on timeout.
        """
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Approve", callback_data="scope:approve"),
                    InlineKeyboardButton("Reject", callback_data="scope:reject"),
                    InlineKeyboardButton("Revise", callback_data="scope:revise"),
                ]
            ]
        )
        await self._app.bot.send_message(self._allowed_chat_id, prompt, reply_markup=keyboard)
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_str["scope_approval"] = fut
        try:
            choice = await asyncio.wait_for(fut, timeout=timeout_s)
        except TimeoutError:
            self._pending_str.pop("scope_approval", None)
            log.warning("telegram_scope_approval_timeout")
            return None
        self._pending_str.pop("scope_approval", None)
        if choice == "revise":
            return await self.ask_free_text("What changes would you like?", timeout_s=timeout_s)
        return choice  # "approve" or "reject"

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
