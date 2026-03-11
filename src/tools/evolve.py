"""Self-evolution gateway — propose_tool stages new tools for user approval."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

import structlog

from ..guardrails.code_scanner import CodeScanner

log = structlog.get_logger()


class EvolveNotifier(Protocol):
    async def send_diff(self, tool_name: str, description: str, code: str, code_hash: str) -> None:
        """Send proposed tool diff to user for approval."""
        ...

    async def wait_for_approval(self, tool_name: str) -> bool:
        """Wait for user YES/NO. Returns True if approved."""
        ...


class ProposeTool:
    """
    Meta-tool: agent proposes a new tool.
    Code is scanned, staged to tools_pending/, user is notified.
    """
    name = "propose_tool"
    description = (
        "Propose a new tool to extend your capabilities. "
        "The code will be scanned for safety and sent to the user for approval. "
        "The tool class MUST follow this exact interface:\n"
        "  - class attributes: name (str), description (str), input_schema (dict)\n"
        "  - async method: execute(self, **kwargs: Any) -> str\n"
        "  - NO __init__ arguments (no dependency injection)\n"
        "Example skeleton:\n"
        "  from typing import Any\n"
        "  class MyTool:\n"
        "      name = 'my_tool'\n"
        "      description = 'what it does'\n"
        "      input_schema: dict[str, Any] = {'type': 'object', 'properties': {'x': {'type': 'string'}}, 'required': ['x']}\n"
        "      async def execute(self, **kwargs: Any) -> str:\n"
        "          return kwargs['x']"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Tool name (snake_case)"},
            "description": {"type": "string"},
            "code": {"type": "string", "description": "Full Python source for the tool class"},
        },
        "required": ["name", "description", "code"],
    }

    def __init__(
        self,
        pending_dir: Path,
        tools_dir: Path,
        notifier: EvolveNotifier,
    ) -> None:
        self._pending = pending_dir
        self._tools_dir = tools_dir
        self._notifier = notifier
        self._scanner = CodeScanner()
        self._pending.mkdir(parents=True, exist_ok=True)
        self._tools_dir.mkdir(parents=True, exist_ok=True)

    async def execute(self, **kwargs: Any) -> str:
        name: str = kwargs["name"]
        description: str = kwargs["description"]
        code: str = kwargs["code"]

        # Validate name
        if not name.replace("_", "").isalnum() or not name.islower():
            return f"Invalid tool name '{name}'. Must be snake_case alphanumeric."

        # Scan code
        scan = self._scanner.scan(code, filename=f"tools/{name}.py")
        if scan.blocked:
            log.warning("propose_tool_blocked", name=name, reason=scan.reason)
            return f"[BLOCKED by code scanner] {scan.reason}"

        # Write to staging
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        pending_path = self._pending / f"{name}.py"
        pending_path.write_text(code)

        meta = {"name": name, "description": description, "code_hash": code_hash}
        (self._pending / f"{name}.json").write_text(json.dumps(meta, indent=2))

        log.info("propose_tool_staged", name=name, hash=code_hash[:12])

        # Notify user
        await self._notifier.send_diff(name, description, code, code_hash)
        approved = await self._notifier.wait_for_approval(name)

        if not approved:
            pending_path.unlink(missing_ok=True)
            (self._pending / f"{name}.json").unlink(missing_ok=True)
            return f"Tool '{name}' rejected by user."

        # Move to tools/
        target = self._tools_dir / f"{name}.py"
        target.write_text(code)
        pending_path.unlink(missing_ok=True)
        (self._pending / f"{name}.json").unlink(missing_ok=True)

        log.info("propose_tool_approved", name=name, hash=code_hash[:12])

        # Activate immediately — no restart needed for new tool files
        try:
            from .loader import load_tools_from_dir
            newly = load_tools_from_dir(self._tools_dir)
            if name in newly:
                return f"Tool '{name}' approved, registered, and live. SHA256: {code_hash[:16]}"
            else:
                return (
                    f"Tool '{name}' approved and saved, but auto-registration failed. "
                    f"The class must have: name (str), description (str), input_schema (dict), "
                    f"and async execute(self, **kwargs) -> str. No __init__ args allowed. "
                    f"SHA256: {code_hash[:16]}"
                )
        except Exception as exc:
            log.warning("tool_auto_activate_failed", name=name, error=str(exc))
            return (
                f"Tool '{name}' saved but activation error: {exc}. SHA256: {code_hash[:16]}"
            )
