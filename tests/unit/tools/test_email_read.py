"""Tests for EmailReadTool — IMAP read-only flow."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.email_read import EmailReadTool


@pytest.fixture
def tool() -> EmailReadTool:
    return EmailReadTool(
        imap_host="imap.example.com",
        imap_user="user@example.com",
        imap_password="secret",
    )


def _make_imap(messages: list[tuple[str, str, str]]) -> MagicMock:
    """Build a mock IMAP4_SSL that returns the given (subject, sender, date) tuples."""
    import email as email_lib
    from email.mime.text import MIMEText

    imap = MagicMock()
    imap.__enter__ = MagicMock(return_value=imap)
    imap.__exit__ = MagicMock(return_value=False)

    # login / select / logout are no-ops
    imap.login = MagicMock()
    imap.select = MagicMock(return_value=("OK", [b"5"]))
    imap.logout = MagicMock()

    # Build message IDs
    ids = " ".join(str(i + 1) for i in range(len(messages))).encode()
    imap.search = MagicMock(return_value=("OK", [ids]))

    # Build raw headers for each message
    def _fetch(msg_id: bytes, spec: str) -> tuple[str, list]:
        idx = int(msg_id) - 1
        subject, sender, date = messages[idx]
        msg = MIMEText("body")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["Date"] = date
        raw = msg.as_bytes()
        return ("OK", [(b"1 RFC822.HEADER", raw)])

    imap.fetch = MagicMock(side_effect=_fetch)
    return imap


# ---------------------------------------------------------------------------
# Basic flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returns_messages(tool: EmailReadTool) -> None:
    mock_imap = _make_imap([
        ("Hello world", "alice@example.com", "Mon, 1 Jan 2024 10:00:00 +0000"),
    ])
    with patch("src.tools.email_read.imaplib.IMAP4_SSL", return_value=mock_imap):
        result = await tool.execute(count=5, unread_only=True)
    assert "Hello world" in result
    assert "alice@example.com" in result


@pytest.mark.asyncio
async def test_no_messages_returns_message(tool: EmailReadTool) -> None:
    imap = MagicMock()
    imap.login = MagicMock()
    imap.select = MagicMock(return_value=("OK", [b"0"]))
    imap.search = MagicMock(return_value=("OK", [b""]))
    imap.logout = MagicMock()

    with patch("src.tools.email_read.imaplib.IMAP4_SSL", return_value=imap):
        result = await tool.execute()
    assert "No messages" in result


@pytest.mark.asyncio
async def test_imap_error_returns_error_string(tool: EmailReadTool) -> None:
    with patch(
        "src.tools.email_read.imaplib.IMAP4_SSL",
        side_effect=ConnectionRefusedError("refused"),
    ):
        result = await tool.execute()
    assert "error" in result.lower() or "Error" in result


# ---------------------------------------------------------------------------
# Read-only — no smtplib import anywhere in the file
# ---------------------------------------------------------------------------

def test_smtplib_not_imported() -> None:
    import ast
    import pathlib
    src = pathlib.Path("src/tools/email_read.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            assert not any("smtp" in n.lower() for n in names), \
                "smtplib must never be imported in email_read.py"
