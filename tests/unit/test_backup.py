"""Tests for cloud backup — run_backup()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.backup import run_backup

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_disabled_when_no_repo(tmp_path: Path) -> None:
    """Empty backup_repo should return early with a message."""
    result = await run_backup(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        backup_repo="",
    )
    assert "disabled" in result.lower()


@pytest.mark.asyncio
async def test_no_dbs_returns_summary(tmp_path: Path) -> None:
    """Empty data dir (no DBs) — still succeeds with 0 DBs dumped."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    async def _fake_run_cmd(*args: str, cwd: Path | None = None) -> tuple[int, bytes, bytes]:
        # gh repo clone → create the clone dir
        if args[0] == "gh" and "clone" in args:
            clone_dir = Path(args[4])
            clone_dir.mkdir(parents=True, exist_ok=True)
        return (0, b"", b"")

    with patch("src.backup._run_cmd", side_effect=_fake_run_cmd):
        result = await run_backup(
            data_dir=data_dir,
            memory_dir=memory_dir,
            backup_repo="ruidpm/enki-state",
        )

    assert "0 DBs" in result or "0 db" in result.lower()
    assert "error" not in result.lower()


@pytest.mark.asyncio
async def test_skips_missing_dbs(tmp_path: Path) -> None:
    """Only dumps DBs that exist on disk."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Create only 2 DBs
    (data_dir / "audit.db").write_text("fake")
    (data_dir / "tasks.db").write_text("fake")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    sqlite3_calls: list[tuple[str, ...]] = []

    async def _fake_run_cmd(*args: str, cwd: Path | None = None) -> tuple[int, bytes, bytes]:
        if args[0] == "sqlite3":
            sqlite3_calls.append(args)
            return (0, b"-- SQL dump", b"")
        if args[0] == "gh" and "clone" in args:
            clone_dir = Path(args[4])
            clone_dir.mkdir(parents=True, exist_ok=True)
        return (0, b"", b"")

    with patch("src.backup._run_cmd", side_effect=_fake_run_cmd):
        result = await run_backup(
            data_dir=data_dir,
            memory_dir=memory_dir,
            backup_repo="ruidpm/enki-state",
        )

    # Should have called sqlite3 exactly 2 times (for audit.db and tasks.db)
    assert len(sqlite3_calls) == 2
    assert "2 DBs" in result


@pytest.mark.asyncio
async def test_git_nothing_to_commit_is_ok(tmp_path: Path) -> None:
    """git commit returning rc=1 (nothing to commit) is not an error."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "audit.db").write_text("fake")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    async def _fake_run_cmd(*args: str, cwd: Path | None = None) -> tuple[int, bytes, bytes]:
        if args[0] == "sqlite3":
            return (0, b"-- SQL dump", b"")
        if args[0] == "gh" and "clone" in args:
            clone_dir = Path(args[4])
            clone_dir.mkdir(parents=True, exist_ok=True)
        if args[0] == "git" and len(args) > 1 and args[1] == "commit":
            return (1, b"", b"nothing to commit, working tree clean")
        return (0, b"", b"")

    with patch("src.backup._run_cmd", side_effect=_fake_run_cmd):
        result = await run_backup(
            data_dir=data_dir,
            memory_dir=memory_dir,
            backup_repo="ruidpm/enki-state",
        )

    assert "nothing" in result.lower()
    # Should NOT be treated as an error
    assert "backup error" not in result.lower()


@pytest.mark.asyncio
async def test_backup_copies_entire_memory_dir(tmp_path: Path) -> None:
    """Backup should copy the entire memory directory including patterns.md."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "facts.md").write_text("- User fact\n")
    (memory_dir / "patterns.md").write_text("- User pattern\n")
    logs_dir = memory_dir / "logs"
    logs_dir.mkdir()
    (logs_dir / "2026-03-14.md").write_text("some log\n")

    copied_files: list[str] = []

    async def _fake_run_cmd(*args: str, cwd: Path | None = None) -> tuple[int, bytes, bytes]:
        if args[0] == "gh" and "clone" in args:
            clone_dir = Path(args[4])
            clone_dir.mkdir(parents=True, exist_ok=True)
        if args[0] == "git" and len(args) > 1 and args[1] == "add" and cwd:
            for f in Path(cwd).rglob("*"):
                if f.is_file():
                    copied_files.append(str(f.relative_to(cwd)))
        return (0, b"", b"")

    with patch("src.backup._run_cmd", side_effect=_fake_run_cmd):
        result = await run_backup(
            data_dir=data_dir,
            memory_dir=memory_dir,
            backup_repo="ruidpm/enki-state",
        )

    # Should include all memory files in the count
    assert "3 memory files" in result
    assert "error" not in result.lower()


@pytest.mark.asyncio
async def test_sqlite_dump_failure_continues(tmp_path: Path) -> None:
    """One DB failing to dump doesn't stop others."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "audit.db").write_text("fake")
    (data_dir / "tasks.db").write_text("fake")
    (data_dir / "memory.db").write_text("fake")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    dump_count = 0

    async def _fake_run_cmd(*args: str, cwd: Path | None = None) -> tuple[int, bytes, bytes]:
        nonlocal dump_count
        if args[0] == "sqlite3":
            dump_count += 1
            # Fail the first sqlite3 call
            if dump_count == 1:
                return (1, b"", b"database is locked")
            return (0, b"-- SQL dump", b"")
        if args[0] == "gh" and "clone" in args:
            clone_dir = Path(args[4])
            clone_dir.mkdir(parents=True, exist_ok=True)
        return (0, b"", b"")

    with patch("src.backup._run_cmd", side_effect=_fake_run_cmd):
        result = await run_backup(
            data_dir=data_dir,
            memory_dir=memory_dir,
            backup_repo="ruidpm/enki-state",
        )

    # Should have attempted all 3 dumps
    assert dump_count == 3
    # Should still complete (not crash)
    assert result  # non-empty string
    # 2 succeeded out of 3
    assert "2 DBs" in result
