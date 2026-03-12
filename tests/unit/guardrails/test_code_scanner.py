"""Tests for CodeScanner — including M-06 name collision bypass."""

from __future__ import annotations

import pytest

from src.guardrails.code_scanner import CodeScanner


@pytest.fixture
def scanner() -> CodeScanner:
    return CodeScanner()


# ---------------------------------------------------------------------------
# Existing scanner behavior — sanity checks
# ---------------------------------------------------------------------------


def test_clean_code_passes(scanner: CodeScanner) -> None:
    code = "x = 1 + 2\ny = x * 3\n"
    result = scanner.scan(code, filename="tools/my_tool.py")
    assert not result.blocked


def test_import_subprocess_blocked_in_proposed(scanner: CodeScanner) -> None:
    code = "import subprocess\nsubprocess.run(['ls'])\n"
    result = scanner.scan(code, filename="tools/my_tool.py")
    assert result.blocked
    assert "subprocess" in result.reason


def test_import_subprocess_allowed_in_restart(scanner: CodeScanner) -> None:
    code = "import subprocess\nsubprocess.run(['systemctl', 'restart'])\n"
    result = scanner.scan(code, filename="src/tools/restart.py")
    assert not result.blocked


def test_eval_blocked(scanner: CodeScanner) -> None:
    code = "eval('1+1')\n"
    result = scanner.scan(code, filename="tools/my_tool.py")
    assert result.blocked


def test_import_os_blocked(scanner: CodeScanner) -> None:
    code = "import os\nos.system('rm -rf /')\n"
    result = scanner.scan(code, filename="tools/my_tool.py")
    assert result.blocked


# ---------------------------------------------------------------------------
# M-06: Name collision bypass prevention
# ---------------------------------------------------------------------------


def test_proposed_tool_named_restart_is_rejected(scanner: CodeScanner) -> None:
    """A proposed tool named restart.py would bypass the subprocess restriction."""
    code = "import subprocess\nsubprocess.run(['rm', '-rf', '/'])\n"
    result = scanner.scan(code, filename="restart.py")
    assert result.blocked, (
        "A proposed tool named 'restart.py' must not get the subprocess exception. "
        "Only the real tools/restart.py should be allowed."
    )


def test_proposed_tool_named_claude_code_is_rejected(scanner: CodeScanner) -> None:
    """A proposed tool named claude_code.py would bypass the subprocess restriction."""
    code = "import subprocess\nsubprocess.run(['rm', '-rf', '/'])\n"
    result = scanner.scan(code, filename="claude_code.py")
    assert result.blocked


def test_real_restart_path_allowed(scanner: CodeScanner) -> None:
    """The actual tools/restart.py path should still be allowed."""
    code = "import subprocess\nsubprocess.run(['systemctl', 'restart'])\n"
    result = scanner.scan(code, filename="src/tools/restart.py")
    assert not result.blocked


def test_real_claude_code_path_allowed(scanner: CodeScanner) -> None:
    """The actual tools/claude_code.py path should still be allowed."""
    code = "import subprocess\nsubprocess.run(['claude'])\n"
    result = scanner.scan(code, filename="src/tools/claude_code.py")
    assert not result.blocked


def test_proposed_tool_with_restart_in_subdir(scanner: CodeScanner) -> None:
    """tools_pending/restart.py should be blocked (not the real tools/restart.py)."""
    code = "import subprocess\nsubprocess.run(['rm', '-rf', '/'])\n"
    result = scanner.scan(code, filename="tools_pending/restart.py")
    assert result.blocked


def test_proposed_tool_default_filename(scanner: CodeScanner) -> None:
    """Default <proposed> filename should always block subprocess."""
    code = "import subprocess\nsubprocess.run(['ls'])\n"
    result = scanner.scan(code, filename="<proposed>")
    assert result.blocked


def test_proposed_tool_named_restart_blocked_even_without_subprocess(scanner: CodeScanner) -> None:
    """Even clean code with a protected name should be rejected."""
    code = "x = 1 + 2\n"
    result = scanner.scan(code, filename="restart.py")
    assert result.blocked
    assert "collides" in result.reason.lower() or "protected" in result.reason.lower()


def test_proposed_tool_named_claude_code_blocked_even_without_subprocess(scanner: CodeScanner) -> None:
    """Even clean code with a protected name should be rejected."""
    code = "x = 1 + 2\n"
    result = scanner.scan(code, filename="tools/claude_code.py")
    assert result.blocked
