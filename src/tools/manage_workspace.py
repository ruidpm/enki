"""Workspace management tools.

ListWorkspacesTool  — read-only, no confirmation required.
ManageWorkspaceTool — write ops (add/clone/init/remove/set_trust), in REQUIRES_CONFIRM.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from src.workspaces.store import TrustLevel, WorkspaceStore

log = structlog.get_logger()

_TRUST_LABELS: dict[int, str] = {
    TrustLevel.READ_ONLY: "read_only",
    TrustLevel.PROPOSE: "propose",
    TrustLevel.AUTO_COMMIT: "auto_commit",
    TrustLevel.AUTO_PUSH: "auto_push",
    TrustLevel.TRUSTED: "trusted",
}


class ListWorkspacesTool:
    name = "list_workspaces"
    description = (
        "List all registered external project workspaces with their trust level, "
        "language, and last used time."
    )
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, store: WorkspaceStore, workspaces_base_dir: Path = Path("workspaces")) -> None:
        self._store = store
        self._base_dir = workspaces_base_dir

    async def execute(self, **_kwargs: Any) -> str:
        workspaces = self._store.list_all()
        base = str(self._base_dir.resolve())
        if not workspaces:
            return f"No workspaces registered. Use manage_workspace to add one.\nBase dir for new workspaces: {base}"

        lines = [f"Workspace base dir: {base}\n", "Workspaces:\n"]
        for ws in workspaces:
            trust_label = _TRUST_LABELS.get(ws["trust_level"], str(ws["trust_level"]))
            lang = ws["language"] or "unknown"
            last = ws["last_used"] or "never"
            remote = ws["git_remote"] or "local only"
            lines.append(
                f"  {ws['workspace_id']}  {ws['name']}\n"
                f"    path:    {ws['local_path']}\n"
                f"    remote:  {remote}\n"
                f"    lang:    {lang}\n"
                f"    trust:   {trust_label} ({ws['trust_level']})\n"
                f"    used:    {last}\n"
            )
        return "\n".join(lines)


class ManageWorkspaceTool:
    name = "manage_workspace"
    description = (
        "Add, clone, init, remove, or change trust level of a workspace. "
        "Actions: add | clone | init | remove | set_trust. "
        "Use 'init' for brand-new projects — creates the directory, runs git init, and registers it. "
        "Always in REQUIRES_CONFIRM — trust level changes are Tier-1 audited."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "clone", "init", "remove", "set_trust"],
                "description": "Operation to perform. Use 'init' for new projects (creates dir + git init).",
            },
            "workspace_id": {
                "type": "string",
                "description": "Unique identifier for this workspace",
            },
            "name": {"type": "string", "description": "Human-readable name"},
            "local_path": {
                "type": "string",
                "description": "Absolute local path (must exist for 'add'; clone destination for 'clone')",
            },
            "git_remote": {
                "type": "string",
                "description": "GitHub/GitLab URL (required for clone)",
            },
            "language": {
                "type": "string",
                "description": "Primary language: python | typescript | go | rust | ...",
            },
            "description": {"type": "string"},
            "trust_level": {
                "type": "integer",
                "description": "0=read_only 1=propose 2=auto_commit 3=auto_push 4=trusted",
            },
            "github_token_env": {
                "type": "string",
                "description": "Name of env var holding GitHub PAT for this repo",
            },
        },
        "required": ["action"],
    }

    def __init__(self, store: WorkspaceStore, workspaces_base_dir: Path = Path("workspaces")) -> None:
        self._store = store
        self._base_dir = workspaces_base_dir
        # Update local_path description with the actual base dir
        base = str(workspaces_base_dir.resolve())
        self.input_schema["properties"]["local_path"]["description"] = (
            f"Absolute path. For new workspaces, use {base}/<name>."
        )

    async def execute(self, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "")
        dispatch = {
            "add": self._add,
            "clone": self._clone,
            "init": self._init,
            "remove": self._remove,
            "set_trust": self._set_trust,
        }
        handler = dispatch.get(action)
        if handler is None:
            return f"[ERROR] Unknown action '{action}'. Valid: add | clone | init | remove | set_trust"
        return await handler(**kwargs)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _add(self, **kwargs: Any) -> str:
        workspace_id: str = kwargs.get("workspace_id", "").strip()
        name: str = kwargs.get("name", "").strip()
        local_path: str = kwargs.get("local_path", "").strip()

        if not workspace_id:
            return "[ERROR] workspace_id is required."
        if not local_path:
            return "[ERROR] local_path is required."
        if not Path(local_path).exists():
            return f"[ERROR] local_path '{local_path}' not found. Path must exist on disk."

        self._store.add(
            workspace_id,
            name=name or workspace_id,
            local_path=local_path,
            git_remote=kwargs.get("git_remote"),
            language=kwargs.get("language"),
            description=kwargs.get("description"),
            trust_level=kwargs.get("trust_level", TrustLevel.PROPOSE),
            github_token_env=kwargs.get("github_token_env"),
        )
        log.info("workspace_added", workspace_id=workspace_id, local_path=local_path)
        return f"Workspace '{workspace_id}' ({name or workspace_id}) registered at {local_path}."

    async def _init(self, **kwargs: Any) -> str:
        """Create directory + git init + register as new workspace."""
        workspace_id: str = kwargs.get("workspace_id", "").strip()
        name: str = kwargs.get("name", "").strip()
        local_path: str = kwargs.get("local_path", "").strip()

        if not workspace_id:
            return "[ERROR] workspace_id is required."
        if not local_path:
            return "[ERROR] local_path is required."

        path = Path(local_path).expanduser()
        path.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            "git", "init", cwd=str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            log.error("workspace_init_git_failed", workspace_id=workspace_id, error=err)
            return f"[ERROR] git init failed: {err}"

        self._store.add(
            workspace_id,
            name=name or workspace_id,
            local_path=str(path),
            git_remote=kwargs.get("git_remote"),
            language=kwargs.get("language"),
            description=kwargs.get("description"),
            trust_level=kwargs.get("trust_level", TrustLevel.PROPOSE),
            github_token_env=kwargs.get("github_token_env"),
        )
        log.info("workspace_initialized", workspace_id=workspace_id, local_path=str(path))
        return f"Workspace '{workspace_id}' initialized at {path}. Ready for run_pipeline."

    async def _clone(self, **kwargs: Any) -> str:
        workspace_id: str = kwargs.get("workspace_id", "").strip()
        git_remote: str = kwargs.get("git_remote", "").strip()
        local_path: str = kwargs.get("local_path", "").strip()
        name: str = kwargs.get("name", workspace_id).strip()

        if not workspace_id:
            return "[ERROR] workspace_id is required."
        if not git_remote:
            return "[ERROR] git_remote is required for clone."
        if not local_path:
            return "[ERROR] local_path (clone destination) is required."
        if not (git_remote.startswith("https://") or git_remote.startswith("http://") or git_remote.startswith("git@")):
            return "[ERROR] Clone URL must start with https://, http://, or git@ (SSH)."

        proc = await asyncio.create_subprocess_exec(
            "git", "clone", git_remote, local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            log.error("workspace_clone_failed", workspace_id=workspace_id, error=err)
            return f"[ERROR] git clone failed (exit {proc.returncode}): {err}"

        self._store.add(
            workspace_id,
            name=name,
            local_path=local_path,
            git_remote=git_remote,
            language=kwargs.get("language"),
            description=kwargs.get("description"),
            trust_level=min(kwargs.get("trust_level", TrustLevel.PROPOSE), TrustLevel.PROPOSE),
            github_token_env=kwargs.get("github_token_env"),
        )
        log.info("workspace_cloned", workspace_id=workspace_id, git_remote=git_remote)
        return f"Cloned '{git_remote}' → {local_path} and registered as '{workspace_id}'."

    async def _remove(self, **kwargs: Any) -> str:
        workspace_id: str = kwargs.get("workspace_id", "").strip()
        if not workspace_id:
            return "[ERROR] workspace_id is required."

        removed = self._store.remove(workspace_id)
        if not removed:
            return f"[ERROR] Workspace '{workspace_id}' not found."

        log.info("workspace_removed", workspace_id=workspace_id)
        return (
            f"Workspace '{workspace_id}' unregistered. "
            "Local files were NOT deleted — remove them manually if needed."
        )

    async def _set_trust(self, **kwargs: Any) -> str:
        workspace_id: str = kwargs.get("workspace_id", "").strip()
        trust_level = kwargs.get("trust_level")

        if not workspace_id:
            return "[ERROR] workspace_id is required."
        if trust_level is None or trust_level not in TrustLevel.ALL:
            return (
                f"[ERROR] Invalid trust_level '{trust_level}'. "
                "Valid: 0=read_only 1=propose 2=auto_commit 3=auto_push 4=trusted"
            )

        updated = self._store.update_trust(workspace_id, trust_level)
        if not updated:
            return f"[ERROR] Workspace '{workspace_id}' not found."

        label = _TRUST_LABELS[trust_level]
        log.warning(
            "workspace_trust_changed",
            workspace_id=workspace_id,
            trust_level=trust_level,
            trust_label=label,
        )
        return f"Workspace '{workspace_id}' trust level set to {label} ({trust_level})."
