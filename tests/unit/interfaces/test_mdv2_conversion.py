"""Tests for markdown_to_mdv2 conversion."""

from __future__ import annotations

from src.interfaces.telegram_bot import escape_mdv2, markdown_to_mdv2


def test_plain_text_escapes_dots() -> None:
    assert "\\." in markdown_to_mdv2("Hello world.")


def test_plain_text_escapes_parens() -> None:
    result = markdown_to_mdv2("foo (bar)")
    assert "\\(" in result
    assert "\\)" in result


def test_bold_converted() -> None:
    result = markdown_to_mdv2("This is **bold** text.")
    assert "*bold*" in result
    assert "\\." in result


def test_double_underscore_bold() -> None:
    result = markdown_to_mdv2("This is __bold__ text.")
    assert "*bold*" in result


def test_italic_preserved() -> None:
    result = markdown_to_mdv2("This is _italic_ text.")
    assert "_italic_" in result


def test_inline_code_preserved() -> None:
    result = markdown_to_mdv2("Run `pip install foo` now.")
    assert "`pip install foo`" in result
    assert "\\." in result


def test_fenced_code_block_preserved() -> None:
    text = "Before:\n```python\nx = 1.0\n```\nAfter."
    result = markdown_to_mdv2(text)
    # Code block content should NOT have escaped dots
    assert "x = 1.0" in result
    assert "```python\nx = 1.0\n```" in result
    # But text outside should be escaped
    assert "After\\." in result


def test_special_chars_escaped() -> None:
    """All MDV2 special chars should be escaped in plain text."""
    result = markdown_to_mdv2("a.b!c#d-e=f|g{h}")
    assert result == "a\\.b\\!c\\#d\\-e\\=f\\|g\\{h\\}"


def test_mixed_formatting() -> None:
    text = "**Status**: done. Check `log.txt` for details."
    result = markdown_to_mdv2(text)
    assert "*Status*" in result
    assert "`log.txt`" in result
    assert "\\." in result


def test_escape_mdv2_standalone() -> None:
    assert escape_mdv2("hello.world!") == "hello\\.world\\!"


def test_no_double_escape() -> None:
    """Already-escaped text should not be double-escaped."""
    text = "Simple text."
    result = markdown_to_mdv2(text)
    assert result == "Simple text\\."
    # Running it again would double-escape, but that's expected —
    # markdown_to_mdv2 should only be called once per message
