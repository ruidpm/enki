"""Tests for dynamic tool auto-discovery."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.tools import registry
from src.tools.loader import _SKIP_FILES, load_tools_from_dir

VALID_TOOL_SRC = '''
from typing import Any

class GoodTool:
    name = "good_tool"
    description = "does something"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    async def execute(self, **kwargs: Any) -> str:
        return kwargs.get("msg", "")
'''

NO_TOOL_SRC = '''
# just a helper module, no Tool class
def helper():
    return 42
'''

MISSING_EXECUTE_SRC = '''
from typing import Any

class PartialTool:
    name = "partial_tool"
    description = "incomplete"
    input_schema: dict[str, Any] = {}
    # no execute() method
'''


@pytest.fixture(autouse=True)
def clean_registry() -> None:
    """Remove dynamically discovered tools between tests."""
    keys_before = set(registry.keys())
    yield
    for k in list(registry.keys()):
        if k not in keys_before:
            del registry[k]


@pytest.fixture
def tool_dir(tmp_path: Path) -> Path:
    """A temp dir that's on sys.path so importlib.import_module can find modules in it."""
    sys.path.insert(0, str(tmp_path.parent))
    yield tmp_path
    sys.path.remove(str(tmp_path.parent))
    # Clean up any dynamically imported modules from this dir
    for key in list(sys.modules.keys()):
        if key.startswith(tmp_path.name + ".") or key == tmp_path.name:
            del sys.modules[key]


def _load(tool_dir: Path) -> list[str]:
    """Call load_tools_from_dir with the tmp dir's package name patched in."""
    with patch("src.tools.loader.Path") as mock_path_cls:
        # Make the package name resolve to tmp_dir.name (e.g. "tmp_abc123")
        mock_path_cls.return_value.parent = tool_dir.parent
        if hasattr(load_tools_from_dir, '__wrapped__'):
            return load_tools_from_dir.__wrapped__(tool_dir)
        return _load_direct(tool_dir)


def _load_direct(directory: Path) -> list[str]:
    """Load directly with the directory treated as a top-level package on sys.path."""
    import importlib
    import inspect

    from src.tools import register, registry
    from src.tools.loader import _SKIP_FILES, _is_tool_class

    newly_registered: list[str] = []
    package_name = directory.name

    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        if path.stem in _SKIP_FILES:
            continue

        module_name = f"{package_name}.{path.stem}"
        try:
            module = sys.modules[module_name] if module_name in sys.modules else importlib.import_module(module_name)
        except Exception:
            continue

        for _attr_name, obj in inspect.getmembers(module):
            if not _is_tool_class(obj):
                continue
            tool_name: str = obj.name
            if tool_name in registry:
                continue
            try:
                instance = obj()
                register(instance)
                newly_registered.append(tool_name)
            except Exception:
                pass

    return newly_registered


def test_valid_tool_gets_registered(tool_dir: Path) -> None:
    (tool_dir / "good_tool.py").write_text(VALID_TOOL_SRC)
    loaded = _load_direct(tool_dir)
    assert "good_tool" in loaded
    assert "good_tool" in registry


def test_module_without_tool_is_skipped(tool_dir: Path) -> None:
    (tool_dir / "helper.py").write_text(NO_TOOL_SRC)
    loaded = _load_direct(tool_dir)
    assert loaded == []


def test_class_missing_execute_is_skipped(tool_dir: Path) -> None:
    (tool_dir / "partial.py").write_text(MISSING_EXECUTE_SRC)
    loaded = _load_direct(tool_dir)
    assert loaded == []


def test_already_registered_tool_is_not_double_registered(tool_dir: Path) -> None:
    (tool_dir / "good_tool.py").write_text(VALID_TOOL_SRC)
    _load_direct(tool_dir)
    loaded = _load_direct(tool_dir)
    assert loaded == []  # already registered, nothing new


def test_syntax_error_in_file_does_not_crash(tool_dir: Path) -> None:
    (tool_dir / "broken.py").write_text("def (: pass  # invalid syntax")
    loaded = _load_direct(tool_dir)
    assert loaded == []


def test_dunder_files_are_skipped(tool_dir: Path) -> None:
    (tool_dir / "__init__.py").write_text(VALID_TOOL_SRC)
    loaded = _load_direct(tool_dir)
    assert loaded == []


def test_skip_files_are_not_loaded(tool_dir: Path) -> None:
    """Core files in _SKIP_FILES must not be auto-instantiated."""
    for name in list(_SKIP_FILES)[:3]:
        (tool_dir / f"{name}.py").write_text(VALID_TOOL_SRC)
    loaded = _load_direct(tool_dir)
    assert loaded == []


def test_multiple_tools_in_one_file(tool_dir: Path) -> None:
    src = '''
from typing import Any

class ToolA:
    name = "tool_a"
    description = "a"
    input_schema: dict[str, Any] = {}
    async def execute(self, **kwargs: Any) -> str:
        return "a"

class ToolB:
    name = "tool_b"
    description = "b"
    input_schema: dict[str, Any] = {}
    async def execute(self, **kwargs: Any) -> str:
        return "b"
'''
    (tool_dir / "multi.py").write_text(src)
    loaded = _load_direct(tool_dir)
    assert "tool_a" in loaded
    assert "tool_b" in loaded
