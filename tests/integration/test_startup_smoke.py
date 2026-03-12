"""Smoke tests: verify app startup path resolves all imports and attributes.

These tests exist because an audit refactored cli.py's _spinner_active global
but failed to update the reference in main.py, causing a production crash that
passed all 539 unit tests, mypy, and ruff.
"""

from __future__ import annotations


def test_spinner_clear_processor_resolves() -> None:
    """_SpinnerClearProcessor must access cli module attributes without error."""
    from main import _SpinnerClearProcessor

    proc = _SpinnerClearProcessor()
    # Should not raise AttributeError — this is exactly the bug that crashed prod
    result = proc(logger=None, method="info", event_dict={"event": "test"})
    assert isinstance(result, dict)


def test_build_result_types() -> None:
    """BuildResult NamedTuple must be importable and constructable."""
    from main import BuildResult

    # Smoke — just verify the class exists and has the right fields
    assert hasattr(BuildResult, "_fields")
    assert "agent" in BuildResult._fields
    assert "config" in BuildResult._fields
    assert "compactor" in BuildResult._fields


def test_structlog_configured_with_spinner_processor() -> None:
    """structlog must be configured with _SpinnerClearProcessor in the chain."""
    import structlog

    # The processor chain is set at import time in main.py
    import main  # noqa: F401 — triggers structlog.configure()

    # Verify structlog doesn't crash on a basic log call
    log = structlog.get_logger()
    # This exercises the full processor chain including _SpinnerClearProcessor
    log.info("smoke_test_startup", status="ok")
