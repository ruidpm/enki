"""Email read tool — IMAP read-only. smtplib is intentionally absent from this codebase."""

from __future__ import annotations

import asyncio
import email
import imaplib
from email.header import decode_header
from typing import Any

import structlog

log = structlog.get_logger()


def _decode_header_value(value: str) -> str:
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


class EmailReadTool:
    name = "email_read"
    description = (
        "Read recent emails via IMAP. Read-only — cannot send or modify emails. "
        "Returns subject, sender, and snippet for recent unread messages."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "default": 10, "minimum": 1, "maximum": 20},
            "folder": {"type": "string", "default": "INBOX"},
            "unread_only": {"type": "boolean", "default": True},
        },
    }

    def __init__(self, imap_host: str, imap_user: str, imap_password: str) -> None:
        self._host = imap_host
        self._user = imap_user
        self._password = imap_password

    async def execute(self, **kwargs: Any) -> str:
        count = min(int(kwargs.get("count", 10)), 20)
        folder = kwargs.get("folder", "INBOX")
        unread_only = bool(kwargs.get("unread_only", True))

        # Run blocking IMAP in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_emails, count, folder, unread_only)

    def _fetch_emails(self, count: int, folder: str, unread_only: bool) -> str:
        try:
            mail = imaplib.IMAP4_SSL(self._host)
            mail.login(self._user, self._password)
            mail.select(folder, readonly=True)  # readonly=True enforces read-only

            criteria = "UNSEEN" if unread_only else "ALL"
            _, data = mail.search(None, criteria)
            ids = data[0].split()
            if not ids:
                return "No messages found."

            recent_ids = ids[-count:]
            results = []
            for msg_id in reversed(recent_ids):
                _, msg_data = mail.fetch(msg_id, "(RFC822.HEADER)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
                msg = email.message_from_bytes(raw)
                subject = _decode_header_value(msg.get("Subject", "(no subject)"))
                sender = _decode_header_value(msg.get("From", "(unknown)"))
                date = msg.get("Date", "")
                results.append(f"From: {sender}\nSubject: {subject}\nDate: {date}")

            mail.logout()
            return "\n\n".join(results) if results else "No messages."
        except Exception as exc:
            log.error("email_read_error", error=str(exc))
            return f"Email read error: {exc}"
