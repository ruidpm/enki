"""Deterministic browser pre-check for web projects — no LLM involved.

Opens HTML files in headless Chromium via Playwright, captures console errors,
JS exceptions, and accessibility tree. Returns a structured text report.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

log = structlog.get_logger()

try:
    from playwright.async_api import async_playwright  # type: ignore[import-not-found,unused-ignore]

    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False
    async_playwright = None  # type: ignore[assignment,unused-ignore]


async def run_browser_check(workspace_path: str) -> str:
    """Run a headless browser check on HTML files in the workspace.

    Returns a structured report string, or "" if no HTML files found
    or Playwright is not available.
    """
    ws = Path(workspace_path)
    html_files = sorted(ws.glob("*.html"))
    if not html_files:
        return ""

    # Prefer index.html
    target = next((f for f in html_files if f.name == "index.html"), html_files[0])

    if not _HAS_PLAYWRIGHT:
        log.warning("browser_check_skipped", reason="playwright not installed")
        return ""

    console_errors: list[str] = []
    js_exceptions: list[str] = []
    accessibility_tree: str = ""

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            def _on_console(msg: object) -> None:
                msg_type = getattr(msg, "type", "")
                msg_text = getattr(msg, "text", "")
                if msg_type in ("error", "warning"):
                    console_errors.append(f"[{msg_type}] {msg_text}")

            page.on("console", _on_console)
            page.on("pageerror", lambda exc: js_exceptions.append(str(exc)))

            await page.goto(f"file://{target.resolve()}", wait_until="load", timeout=15000)

            snapshot = await page.accessibility.snapshot()  # type: ignore[attr-defined,unused-ignore]
            if snapshot:
                accessibility_tree = json.dumps(snapshot, indent=2)[:2000]

            await browser.close()
    except Exception as exc:
        log.warning("browser_check_failed", error=str(exc), file=str(target))
        return ""

    # Build report
    lines = [
        "## Browser Pre-Check",
        f"File: {target.name}",
        "",
        "### Console Errors",
        "\n".join(console_errors) if console_errors else "None",
        "",
        "### JS Exceptions",
        "\n".join(js_exceptions) if js_exceptions else "None",
        "",
        "### Page Structure",
        accessibility_tree if accessibility_tree else "(empty page)",
    ]

    report = "\n".join(lines)
    log.info("browser_check_complete", file=target.name, errors=len(console_errors), exceptions=len(js_exceptions))
    return report
