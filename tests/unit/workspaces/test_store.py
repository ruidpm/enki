"""Tests for WorkspaceStore."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.workspaces.store import TrustLevel, WorkspaceStore


@pytest.fixture
def store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path / "workspaces.db")


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------

def test_add_and_get(store: WorkspaceStore) -> None:
    store.add("ws1", name="MyApp", local_path="/projects/myapp")
    ws = store.get("ws1")
    assert ws is not None
    assert ws["name"] == "MyApp"
    assert ws["local_path"] == "/projects/myapp"
    assert ws["trust_level"] == TrustLevel.PROPOSE
    assert ws["git_remote"] is None
    assert ws["language"] is None


def test_add_with_all_fields(store: WorkspaceStore) -> None:
    store.add(
        "ws2",
        name="API",
        local_path="/projects/api",
        git_remote="https://github.com/user/api",
        language="python",
        description="REST API",
        trust_level=TrustLevel.AUTO_COMMIT,
        github_token_env="API_GITHUB_TOKEN",
    )
    ws = store.get("ws2")
    assert ws is not None
    assert ws["git_remote"] == "https://github.com/user/api"
    assert ws["language"] == "python"
    assert ws["trust_level"] == TrustLevel.AUTO_COMMIT
    assert ws["github_token_env"] == "API_GITHUB_TOKEN"


def test_get_unknown_returns_none(store: WorkspaceStore) -> None:
    assert store.get("nope") is None


def test_list_all_empty(store: WorkspaceStore) -> None:
    assert store.list_all() == []


def test_list_all_returns_all(store: WorkspaceStore) -> None:
    store.add("a", name="A", local_path="/a")
    store.add("b", name="B", local_path="/b")
    items = store.list_all()
    assert len(items) == 2
    ids = {w["workspace_id"] for w in items}
    assert ids == {"a", "b"}


def test_remove_existing(store: WorkspaceStore) -> None:
    store.add("ws1", name="X", local_path="/x")
    assert store.remove("ws1") is True
    assert store.get("ws1") is None


def test_remove_nonexistent_returns_false(store: WorkspaceStore) -> None:
    assert store.remove("ghost") is False


# ---------------------------------------------------------------------------
# Trust level
# ---------------------------------------------------------------------------

def test_update_trust_level(store: WorkspaceStore) -> None:
    store.add("ws1", name="X", local_path="/x")
    assert store.update_trust("ws1", TrustLevel.TRUSTED) is True
    ws = store.get("ws1")
    assert ws is not None
    assert ws["trust_level"] == TrustLevel.TRUSTED


def test_update_trust_unknown_returns_false(store: WorkspaceStore) -> None:
    assert store.update_trust("ghost", TrustLevel.TRUSTED) is False


def test_trust_level_values_are_ordered() -> None:
    assert TrustLevel.READ_ONLY < TrustLevel.PROPOSE
    assert TrustLevel.PROPOSE < TrustLevel.AUTO_COMMIT
    assert TrustLevel.AUTO_COMMIT < TrustLevel.AUTO_PUSH
    assert TrustLevel.AUTO_PUSH < TrustLevel.TRUSTED


# ---------------------------------------------------------------------------
# touch / last_used
# ---------------------------------------------------------------------------

def test_touch_updates_last_used(store: WorkspaceStore) -> None:
    store.add("ws1", name="X", local_path="/x")
    ws_before = store.get("ws1")
    assert ws_before is not None
    assert ws_before["last_used"] is None

    store.touch("ws1")
    ws_after = store.get("ws1")
    assert ws_after is not None
    assert ws_after["last_used"] is not None


def test_touch_unknown_is_noop(store: WorkspaceStore) -> None:
    store.touch("ghost")  # must not raise


# ---------------------------------------------------------------------------
# Duplicate add (idempotent upsert)
# ---------------------------------------------------------------------------

def test_add_duplicate_overwrites(store: WorkspaceStore) -> None:
    store.add("ws1", name="Old", local_path="/old")
    store.add("ws1", name="New", local_path="/new")
    ws = store.get("ws1")
    assert ws is not None
    assert ws["name"] == "New"
    assert ws["local_path"] == "/new"
