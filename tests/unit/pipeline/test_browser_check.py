"""Tests for browser_check — deterministic Playwright pre-check for web projects."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.pipeline.browser_check import run_browser_check


@pytest.mark.asyncio
async def test_no_html_returns_empty(tmp_path: Path) -> None:
    """Workspace without HTML files returns empty string."""
    (tmp_path / "main.py").write_text("print('hello')")
    result = await run_browser_check(str(tmp_path))
    assert result == ""


@pytest.mark.asyncio
async def test_playwright_missing_returns_empty(tmp_path: Path) -> None:
    """When playwright is not installed, return empty string gracefully."""
    (tmp_path / "index.html").write_text("<html><body>Hi</body></html>")

    with patch("src.pipeline.browser_check._HAS_PLAYWRIGHT", False):
        result = await run_browser_check(str(tmp_path))

    assert result == ""


def _mock_playwright(mock_page: AsyncMock) -> AsyncMock:
    """Create a mock async_playwright context manager."""
    mock_browser = AsyncMock()
    mock_browser.new_page = AsyncMock(return_value=mock_page)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


@pytest.mark.asyncio
async def test_html_detected_returns_report(tmp_path: Path) -> None:
    """Workspace with index.html returns a structured report (mocked Playwright)."""
    (tmp_path / "index.html").write_text("<html><body><h1>Hello</h1></body></html>")

    mock_page = AsyncMock()
    mock_page.accessibility.snapshot = AsyncMock(return_value={"role": "document", "name": "", "children": []})

    with (
        patch("src.pipeline.browser_check._HAS_PLAYWRIGHT", True),
        patch("src.pipeline.browser_check.async_playwright", return_value=_mock_playwright(mock_page)),
    ):
        result = await run_browser_check(str(tmp_path))

    assert "## Browser Pre-Check" in result
    assert "File: index.html" in result
    assert "### Console Errors" in result
    assert "### JS Exceptions" in result
    assert "### Page Structure" in result


@pytest.mark.asyncio
async def test_prefers_index_html(tmp_path: Path) -> None:
    """When multiple HTML files exist, index.html is preferred."""
    (tmp_path / "about.html").write_text("<html></html>")
    (tmp_path / "index.html").write_text("<html></html>")

    mock_page = AsyncMock()
    mock_page.accessibility.snapshot = AsyncMock(return_value=None)

    with (
        patch("src.pipeline.browser_check._HAS_PLAYWRIGHT", True),
        patch("src.pipeline.browser_check.async_playwright", return_value=_mock_playwright(mock_page)),
    ):
        result = await run_browser_check(str(tmp_path))

    assert "File: index.html" in result
    call_args = mock_page.goto.call_args
    assert "index.html" in call_args[0][0]


@pytest.mark.asyncio
async def test_browser_launch_failure_returns_empty(tmp_path: Path) -> None:
    """If Playwright fails to launch, return empty string."""
    (tmp_path / "index.html").write_text("<html></html>")

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("No browser"))
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.pipeline.browser_check._HAS_PLAYWRIGHT", True),
        patch("src.pipeline.browser_check.async_playwright", return_value=mock_cm),
    ):
        result = await run_browser_check(str(tmp_path))

    assert result == ""
