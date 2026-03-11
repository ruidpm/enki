"""GitHub tools — via gh CLI. Feature-branch only. No push to main/master.

All tools accept an optional workspace_id parameter.
When provided, operations run in the workspace's local_path directory.
When omitted, operations run in the assistant's own repository (CWD).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

_GH = "gh"
_GIT = "git"
_PROTECTED_BRANCHES = frozenset({"main", "master"})


async def _run(*cmd: str, cwd: str | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode().strip(), stderr.decode().strip()


def _resolve_cwd(workspace_id: str | None, workspace_store: object) -> str | tuple[None, str]:
    """Return the cwd path string, or (None, error_message) if resolution fails."""
    if not workspace_id:
        return None  # type: ignore[return-value]  # use process CWD
    if workspace_store is None:
        return None, "[ERROR] No workspace store configured."
    from src.workspaces.store import WorkspaceStore
    assert isinstance(workspace_store, WorkspaceStore)
    ws = workspace_store.get(workspace_id)
    if ws is None:
        return None, f"[ERROR] Workspace '{workspace_id}' not found. Use list_workspaces."
    local_path: str = ws["local_path"]
    if not Path(local_path).exists():
        return None, f"[ERROR] Workspace path '{local_path}' does not exist on disk."
    return local_path  # type: ignore[return-value]


def _check_trust(workspace_id: str | None, workspace_store: object, required: int) -> str | None:
    """Return error string if workspace trust too low, None if OK or no workspace specified."""
    if not workspace_id:
        return None  # assistant repo — no trust restriction
    if workspace_store is None:
        return None
    from src.workspaces.store import WorkspaceStore, TrustLevel
    assert isinstance(workspace_store, WorkspaceStore)
    ws = workspace_store.get(workspace_id)
    if ws is None:
        return None  # _resolve_cwd will surface the not-found error
    trust: int = ws.get("trust_level", TrustLevel.PROPOSE)
    if trust < required:
        _names = {0: "read_only", 1: "propose", 2: "auto_commit", 3: "auto_push", 4: "trusted"}
        return (
            f"[BLOCKED] Workspace trust level '{_names.get(trust, trust)}' is too low for this "
            f"operation (requires '{_names.get(required, required)}'). "
            f"Use manage_workspace set_trust to elevate."
        )
    return None


_WORKSPACE_ID_SCHEMA: dict[str, Any] = {
    "workspace_id": {
        "type": "string",
        "description": "Optional: run in this workspace instead of the assistant repo",
    }
}


class GitStatusTool:
    name = "git_status"
    description = "Show working tree status (staged/unstaged files). Read-only."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {**_WORKSPACE_ID_SCHEMA},
    }

    def __init__(self, workspace_store: object = None) -> None:
        self._ws_store = workspace_store

    async def execute(self, **kwargs: Any) -> str:
        cwd = _resolve_cwd(kwargs.get("workspace_id"), self._ws_store)
        if isinstance(cwd, tuple):
            return cwd[1]
        rc, out, err = await _run(_GIT, "status", "--short", cwd=cwd)
        return out or "Working tree clean." if rc == 0 else f"Error: {err}"


class GitDiffTool:
    name = "git_diff"
    description = "Show diff of changes. Optionally pass a filename. Read-only."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "Optional file path"},
            **_WORKSPACE_ID_SCHEMA,
        },
    }

    def __init__(self, workspace_store: object = None) -> None:
        self._ws_store = workspace_store

    async def execute(self, **kwargs: Any) -> str:
        cwd = _resolve_cwd(kwargs.get("workspace_id"), self._ws_store)
        if isinstance(cwd, tuple):
            return cwd[1]
        cmd = [_GIT, "diff"]
        if f := kwargs.get("file"):
            cmd.append(f)
        rc, out, err = await _run(*cmd, cwd=cwd)
        return out or "No changes." if rc == 0 else f"Error: {err}"


class GitCommitTool:
    name = "git_commit"
    description = "Create a local git commit. Requires confirmation. Specify files and message."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files to stage. Use ['.'] for all.",
            },
            **_WORKSPACE_ID_SCHEMA,
        },
        "required": ["message", "files"],
    }

    def __init__(self, workspace_store: object = None) -> None:
        self._ws_store = workspace_store

    async def execute(self, **kwargs: Any) -> str:
        from src.workspaces.store import TrustLevel
        if err := _check_trust(kwargs.get("workspace_id"), self._ws_store, TrustLevel.PROPOSE):
            return err
        cwd = _resolve_cwd(kwargs.get("workspace_id"), self._ws_store)
        if isinstance(cwd, tuple):
            return cwd[1]
        files: list[str] = kwargs["files"]
        message: str = kwargs["message"]
        rc, out, err = await _run(_GIT, "add", *files, cwd=cwd)
        if rc != 0:
            return f"git add failed: {err}"
        rc, out, err = await _run(_GIT, "commit", "-m", message, cwd=cwd)
        return out if rc == 0 else f"git commit failed: {err}"


class GitPushBranchTool:
    name = "git_push_branch"
    description = (
        "Push a feature branch to GitHub. "
        "NEVER pushes to main or master — those are hard-blocked."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "branch": {"type": "string"},
            **_WORKSPACE_ID_SCHEMA,
        },
        "required": ["branch"],
    }

    def __init__(self, workspace_store: object = None) -> None:
        self._ws_store = workspace_store

    async def execute(self, **kwargs: Any) -> str:
        branch: str = kwargs["branch"]
        if branch in _PROTECTED_BRANCHES:
            return f"[BLOCKED] Cannot push to protected branch '{branch}'."
        from src.workspaces.store import TrustLevel
        if err := _check_trust(kwargs.get("workspace_id"), self._ws_store, TrustLevel.PROPOSE):
            return err
        cwd = _resolve_cwd(kwargs.get("workspace_id"), self._ws_store)
        if isinstance(cwd, tuple):
            return cwd[1]
        rc, out, err = await _run(_GIT, "push", "-u", "origin", branch, cwd=cwd)
        return out if rc == 0 else f"git push failed: {err}"


class CreatePRTool:
    name = "create_pr"
    description = (
        "Open a pull request on GitHub via gh CLI. Base must be 'main'. "
        "Use workspace_id to open a PR on an external workspace's repository."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "branch": {"type": "string", "description": "Head branch to merge from"},
            **_WORKSPACE_ID_SCHEMA,
        },
        "required": ["title", "branch"],
    }

    def __init__(self, workspace_store: object = None) -> None:
        self._ws_store = workspace_store

    async def execute(self, **kwargs: Any) -> str:
        title = kwargs["title"]
        body = kwargs.get("body", "")
        branch = kwargs["branch"]
        if branch in _PROTECTED_BRANCHES:
            return f"[BLOCKED] Cannot open PR from protected branch '{branch}'."
        from src.workspaces.store import TrustLevel
        if err := _check_trust(kwargs.get("workspace_id"), self._ws_store, TrustLevel.PROPOSE):
            return err
        cwd = _resolve_cwd(kwargs.get("workspace_id"), self._ws_store)
        if isinstance(cwd, tuple):
            return cwd[1]
        rc, out, err = await _run(
            _GH, "pr", "create",
            "--title", title,
            "--body", body,
            "--head", branch,
            "--base", "main",
            cwd=cwd,
        )
        return out if rc == 0 else f"gh pr create failed: {err}"
