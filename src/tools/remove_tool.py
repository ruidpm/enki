"""Remove (soft-disable) a proposed tool.

Moves the tool's .py file to tools_disabled/ and unregisters it from the
registry. The file is preserved so it can be re-enabled by moving it back.
IMMUTABLE_CORE tools cannot be removed.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import structlog

from src.guardrails.allowlist import IMMUTABLE_CORE

log = structlog.get_logger()


class RemoveToolTool:
    name = "remove_tool"
    description = (
        "Soft-disable a tool you previously proposed. "
        "Moves the tool file to tools_disabled/ and unregisters it — "
        "it can be re-enabled by moving it back. "
        "Cannot remove core system tools."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Name of the tool to disable (e.g. 'my_tool')",
            },
        },
        "required": ["tool_name"],
    }

    def __init__(
        self,
        tools_dir: Path,
        disabled_dir: Path,
        registry: dict[str, Any],
    ) -> None:
        self._tools_dir = tools_dir
        self._disabled_dir = disabled_dir
        self._registry = registry

    async def execute(self, **kwargs: Any) -> str:
        tool_name: str = kwargs.get("tool_name", "").strip()

        if not tool_name:
            return "[ERROR] tool_name is required."

        if tool_name in IMMUTABLE_CORE:
            return f"[ERROR] '{tool_name}' is an immutable core tool and cannot be removed."

        if tool_name not in self._registry:
            return f"[ERROR] Tool '{tool_name}' not found in registry. Use list_tools to see available tools."

        src = self._tools_dir / f"{tool_name}.py"
        if not src.exists():
            return f"[ERROR] Tool '{tool_name}' is registered but no file found at {src}. Cannot disable built-in tools this way."

        self._disabled_dir.mkdir(parents=True, exist_ok=True)
        dest = self._disabled_dir / f"{tool_name}.py"
        shutil.move(str(src), dest)
        del self._registry[tool_name]

        log.info("tool_disabled", tool_name=tool_name, dest=str(dest))
        return (
            f"Tool '{tool_name}' disabled. "
            f"File moved to {dest}. "
            f"Re-enable by moving it back to {self._tools_dir}/ and restarting."
        )
