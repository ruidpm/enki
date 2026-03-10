"""Security tests — code_scanner must block all known bypass techniques."""
from __future__ import annotations

import pytest

from src.guardrails.code_scanner import CodeScanner, ScanResult


@pytest.fixture
def scanner() -> CodeScanner:
    return CodeScanner()


# --- Blocked patterns ---

def test_blocks_subprocess(scanner: CodeScanner) -> None:
    code = "import subprocess\nsubprocess.run(['ls'])"
    result = scanner.scan(code, filename="evil.py")
    assert result.blocked
    assert "subprocess" in result.reason.lower()


def test_blocks_os_system(scanner: CodeScanner) -> None:
    code = "import os\nos.system('rm -rf /')"
    result = scanner.scan(code)
    assert result.blocked


def test_blocks_eval(scanner: CodeScanner) -> None:
    result = scanner.scan("eval('__import__(\"os\").system(\"id\")')")
    assert result.blocked


def test_blocks_exec(scanner: CodeScanner) -> None:
    result = scanner.scan("exec('import os')")
    assert result.blocked


def test_blocks_import_sys(scanner: CodeScanner) -> None:
    result = scanner.scan("import sys\nsys.exit(0)")
    assert result.blocked


def test_blocks_dunder_import(scanner: CodeScanner) -> None:
    result = scanner.scan("__import__('os').system('id')")
    assert result.blocked


def test_blocks_open_write(scanner: CodeScanner) -> None:
    result = scanner.scan("open('/etc/passwd', 'w').write('hacked')")
    assert result.blocked


def test_blocks_non_allowlisted_network(scanner: CodeScanner) -> None:
    result = scanner.scan("import socket\ns = socket.socket()\ns.connect(('evil.com', 80))")
    assert result.blocked


# --- Bypass attempts ---

def test_blocks_obfuscated_import(scanner: CodeScanner) -> None:
    # __builtins__['__import__']('os')
    result = scanner.scan("__builtins__['__import__']('os').system('id')")
    assert result.blocked


def test_blocks_getattr_bypass(scanner: CodeScanner) -> None:
    result = scanner.scan("getattr(__builtins__, '__import__')('subprocess')")
    assert result.blocked


def test_blocks_base64_encoded_exec(scanner: CodeScanner) -> None:
    result = scanner.scan(
        "import base64\nexec(base64.b64decode('aW1wb3J0IG9z').decode())"
    )
    assert result.blocked


def test_blocks_compile_exec(scanner: CodeScanner) -> None:
    result = scanner.scan("exec(compile('import os', '<str>', 'exec'))")
    assert result.blocked


# --- Allowed patterns ---

def test_allows_clean_tool(scanner: CodeScanner) -> None:
    code = """
import asyncio
from typing import Any

class MyTool:
    name = "my_tool"
    description = "does something"
    input_schema: dict[str, Any] = {}

    async def execute(self, **kwargs: Any) -> str:
        return "result"
"""
    result = scanner.scan(code)
    assert not result.blocked, result.reason


def test_allows_aiohttp(scanner: CodeScanner) -> None:
    code = "import aiohttp\n# use aiohttp for HTTP requests"
    result = scanner.scan(code)
    assert not result.blocked


def test_allows_json_pathlib(scanner: CodeScanner) -> None:
    code = "import json\nfrom pathlib import Path\nPath('x').write_text(json.dumps({}))"
    result = scanner.scan(code)
    assert not result.blocked


# --- Additional AST bypass techniques ---

def test_blocks_shutil_module(scanner: CodeScanner) -> None:
    result = scanner.scan("import shutil\nshutil.rmtree('/data')")
    assert result.blocked


def test_blocks_ctypes_module(scanner: CodeScanner) -> None:
    result = scanner.scan("import ctypes\nctypes.cdll.LoadLibrary('evil.so')")
    assert result.blocked


def test_blocks_multiprocessing(scanner: CodeScanner) -> None:
    result = scanner.scan("import multiprocessing\nmultiprocessing.Process(target=evil).start()")
    assert result.blocked


def test_blocks_importlib(scanner: CodeScanner) -> None:
    result = scanner.scan("import importlib\nimportlib.import_module('os').system('id')")
    assert result.blocked


def test_blocks_pty_module(scanner: CodeScanner) -> None:
    result = scanner.scan("import pty\npty.spawn('/bin/sh')")
    assert result.blocked


def test_blocks_from_os_import(scanner: CodeScanner) -> None:
    """from os import system should be blocked."""
    result = scanner.scan("from os import system\nsystem('ls')")
    assert result.blocked


def test_blocks_from_subprocess_import(scanner: CodeScanner) -> None:
    result = scanner.scan("from subprocess import run\nrun(['id'])")
    assert result.blocked


def test_blocks_os_dotted_import(scanner: CodeScanner) -> None:
    """import os.path still imports os — must be blocked."""
    result = scanner.scan("import os.path\nos.system('id')")
    assert result.blocked


def test_blocks_globals_usage(scanner: CodeScanner) -> None:
    result = scanner.scan("g = globals()\ng['__builtins__']['eval']('import os')")
    assert result.blocked


def test_blocks_vars_usage(scanner: CodeScanner) -> None:
    result = scanner.scan("v = vars()\nv['__builtins__']['exec']('import os')")
    assert result.blocked


def test_blocks_setattr_bypass(scanner: CodeScanner) -> None:
    """setattr can be used to monkey-patch guardrails."""
    result = scanner.scan("setattr(some_module, 'execute', evil_fn)")
    assert result.blocked


def test_blocks_delattr_bypass(scanner: CodeScanner) -> None:
    result = scanner.scan("delattr(guardrail, 'check')")
    assert result.blocked


def test_blocks_socket_direct(scanner: CodeScanner) -> None:
    result = scanner.scan("import socket\ns = socket.socket()\ns.connect(('evil.com', 4444))")
    assert result.blocked


def test_blocks_threading_module(scanner: CodeScanner) -> None:
    result = scanner.scan("import threading\nthreading.Thread(target=evil).start()")
    assert result.blocked


def test_blocks_signal_module(scanner: CodeScanner) -> None:
    result = scanner.scan("import signal\nsignal.signal(signal.SIGTERM, evil_handler)")
    assert result.blocked


def test_subprocess_allowed_in_restart_filename(scanner: CodeScanner) -> None:
    """subprocess is allowed in restart.py — the one explicit exception."""
    code = "import subprocess\nsubprocess.Popen(['docker', 'compose', 'restart', 'assistant'])"
    result = scanner.scan(code, filename="tools/restart.py")
    assert not result.blocked, result.reason


def test_subprocess_allowed_in_claude_code_filename(scanner: CodeScanner) -> None:
    """subprocess is allowed in claude_code.py."""
    code = "import subprocess\nsubprocess.run(['claude', '-p', 'task'])"
    result = scanner.scan(code, filename="tools/claude_code.py")
    assert not result.blocked, result.reason


def test_subprocess_blocked_in_other_filenames(scanner: CodeScanner) -> None:
    """subprocess NOT allowed in any other file."""
    for fname in ("evil.py", "tools/tasks.py", "tools/web_search.py", "<proposed>"):
        result = scanner.scan("import subprocess", filename=fname)
        assert result.blocked, f"Expected subprocess to be blocked in {fname!r}"


def test_blocks_syntax_error_gracefully(scanner: CodeScanner) -> None:
    """Malformed code is blocked (SyntaxError → blocked)."""
    result = scanner.scan("def (:(")
    assert result.blocked


def test_blocks_nested_import_obfuscation(scanner: CodeScanner) -> None:
    """Triple-quoted string containing import attempt — regex catches __import__."""
    result = scanner.scan(
        'x = """\\n__import__("os").system("id")\\n"""\nexec(x)'
    )
    assert result.blocked


def test_allows_asyncio(scanner: CodeScanner) -> None:
    """asyncio is a needed stdlib — should be allowed."""
    result = scanner.scan("import asyncio\nawait asyncio.sleep(0)")
    assert not result.blocked


def test_allows_typing(scanner: CodeScanner) -> None:
    result = scanner.scan("from typing import Any, Optional")
    assert not result.blocked


def test_allows_structlog(scanner: CodeScanner) -> None:
    result = scanner.scan("import structlog\nlog = structlog.get_logger()")
    assert not result.blocked
