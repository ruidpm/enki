"""Static code scanner for proposed tools — AST-based, blocks dangerous patterns."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

# CLI binaries that are explicitly permitted in tool code
ALLOWED_SUBPROCESS_BINARIES: frozenset[str] = frozenset(
    {
        "sqlite3",
        "curl",
        "gh",
        "gcalcli",
        "jq",
    }
)

# Modules that are never allowed in proposed tool code
BLOCKED_MODULES: frozenset[str] = frozenset(
    {
        "subprocess",
        "os",
        "sys",
        "shutil",
        "pty",
        "ctypes",
        "socket",
        "multiprocessing",
        "threading",
        "signal",
        "importlib",
        "pkgutil",
        "zipimport",
    }
)

# Dangerous builtins / names
BLOCKED_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "__builtins__",
        "open",  # file writes — tools use CLI for this
        "getattr",
        "setattr",
        "delattr",  # dynamic attribute manipulation
        "globals",
        "locals",
        "vars",
    }
)

# Modules allowed for network access in tool code
ALLOWED_NETWORK_MODULES: frozenset[str] = frozenset(
    {
        "aiohttp",  # only for tools that need HTTP (must still pass scope_check at runtime)
    }
)

_DANGEROUS_PATTERNS = re.compile(
    r"__import__|__builtins__|base64\.b64decode.*exec|compile\(.*exec",
    re.DOTALL,
)


@dataclass
class ScanResult:
    blocked: bool
    reason: str = ""


# Exact paths (with src/ prefix) that are allowed to use subprocess.
# Only the real tool files get the exception — proposed tools never do.
_SUBPROCESS_ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        "src/tools/restart.py",
        "src/tools/claude_code.py",
    }
)

# Protected basenames that must not be used by proposed tools
PROTECTED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "restart",
        "claude_code",
    }
)


def _is_subprocess_allowed(filename: str) -> bool:
    """Check if filename is one of the protected files allowed to use subprocess.

    Only exact known paths are allowed. Proposed tools (which use paths like
    'tools/<name>.py' or '<proposed>') are never allowed.
    """
    # Normalize path separators
    normalized = filename.replace("\\", "/")
    return any(normalized.endswith(path) for path in _SUBPROCESS_ALLOWED_PATHS)


class CodeScanner:
    """
    AST-based static analysis for agent-proposed tool code.
    Exception: subprocess is allowed ONLY in tools/restart.py (enforced by filename).
    """

    def scan(self, code: str, filename: str = "<proposed>") -> ScanResult:
        # Check for protected name collision (M-06)
        basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        stem = basename.removesuffix(".py")
        if stem in PROTECTED_TOOL_NAMES and not _is_subprocess_allowed(filename):
            return ScanResult(
                blocked=True,
                reason=(f"Tool name '{stem}' collides with a protected system tool. Choose a different name."),
            )

        # Regex pre-check for obfuscated patterns
        if _DANGEROUS_PATTERNS.search(code):
            return ScanResult(blocked=True, reason="Dangerous pattern detected (obfuscation attempt)")

        try:
            tree = ast.parse(code, filename=filename)
        except SyntaxError as e:
            return ScanResult(blocked=True, reason=f"Syntax error: {e}")

        for node in ast.walk(tree):
            result = self._check_node(node, filename)
            if result.blocked:
                return result

        return ScanResult(blocked=False)

    def _check_node(self, node: ast.AST, filename: str) -> ScanResult:
        # Import statements
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return self._check_import(node, filename)

        # Name usage (eval, exec, open, etc.)
        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            return ScanResult(blocked=True, reason=f"Blocked builtin: '{node.id}'")

        # Attribute access: os.system, subprocess.run, etc.
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            qualified = f"{node.value.id}.{node.attr}"
            if node.value.id == "subprocess" and _is_subprocess_allowed(filename):
                return ScanResult(blocked=False)
            if node.value.id in BLOCKED_MODULES:
                return ScanResult(blocked=True, reason=f"Blocked: '{qualified}'")

        return ScanResult(blocked=False)

    def _check_import(self, node: ast.Import | ast.ImportFrom, filename: str) -> ScanResult:
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        else:
            names = [node.module.split(".")[0]] if node.module else []

        for name in names:
            if name == "subprocess":
                # Only allowed in tools/restart.py and tools/claude_code.py
                if not _is_subprocess_allowed(filename):
                    return ScanResult(
                        blocked=True,
                        reason=(
                            "subprocess is only allowed in tools/restart.py and tools/claude_code.py. "
                            "Use CLI tool wrappers (sqlite3, curl, gh) instead."
                        ),
                    )
            elif name in BLOCKED_MODULES:
                return ScanResult(blocked=True, reason=f"Blocked module: '{name}'")

        return ScanResult(blocked=False)
