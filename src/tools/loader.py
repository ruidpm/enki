"""Dynamic tool discovery — loads Tool classes from .py files in a directory."""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

import structlog

from . import register, registry

log = structlog.get_logger()

_TOOL_ATTRS = ("name", "description", "input_schema", "execute")

# Core files that require DI args at construction — skip auto-instantiation
_SKIP_FILES = frozenset({
    "tasks", "web_search", "notes", "calendar_read",
    "email_read", "evolve", "restart", "loader",
    "github_tools", "claude_code", "spawn_agent",
})


def _is_tool_class(obj: Any) -> bool:
    """Return True if obj looks like a Tool (has all required attrs, execute is callable)."""
    if not inspect.isclass(obj):
        return False
    if not all(hasattr(obj, attr) for attr in _TOOL_ATTRS):
        return False
    if not callable(getattr(obj, "execute", None)):
        return False
    name = getattr(obj, "name", None)
    return isinstance(name, str) and bool(name)


def load_tools_from_dir(directory: Path) -> list[str]:
    """
    Scan directory for .py files, import each as part of the src.tools package,
    find no-arg Tool classes, register them.
    Returns list of newly registered tool names.
    Errors in individual files are logged and skipped — never crashes.
    """
    newly_registered: list[str] = []

    # Determine the dotted package name for this directory
    # e.g. /app/src/tools -> src.tools
    # We find it by looking for the first parent with an __init__.py chain
    try:
        rel = directory.relative_to(Path(sys.argv[0]).parent)
        package_name = ".".join(rel.parts)
    except ValueError:
        # Fallback: try common known path
        package_name = "src.tools"

    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue  # skip __init__.py, _base.py, etc.
        if path.stem in _SKIP_FILES:
            continue  # skip core files that need DI args

        module_name = f"{package_name}.{path.stem}"

        try:
            if module_name in sys.modules:
                module = sys.modules[module_name]
            else:
                module = importlib.import_module(module_name)
        except Exception as exc:
            log.warning("tool_load_error", file=path.name, error=str(exc))
            continue

        for _attr_name, obj in inspect.getmembers(module):
            if not _is_tool_class(obj):
                continue
            tool_name: str = obj.name
            if tool_name in registry:
                continue  # already registered
            try:
                instance = obj()
                register(instance)
                newly_registered.append(tool_name)
                log.info("tool_auto_registered", name=tool_name, file=path.name)
            except Exception as exc:
                log.warning("tool_register_error", name=tool_name, error=str(exc))

    return newly_registered
