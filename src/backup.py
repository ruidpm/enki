"""Cloud backup — dump SQLite DBs and memory files, push to a GitHub repo."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog

log = structlog.get_logger()

DB_NAMES: list[str] = [
    "audit.db",
    "memory.db",
    "tasks.db",
    "pipelines.db",
    "schedule.db",
    "teams.db",
    "workspaces.db",
    "follow_ups.db",
]


async def _run_cmd(*args: str, cwd: Path | None = None) -> tuple[int, bytes, bytes]:
    """Run a command via asyncio.create_subprocess_exec, return (rc, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout, stderr


async def run_backup(
    *,
    data_dir: Path,
    memory_dir: Path,
    backup_repo: str,
) -> str:
    """Dump DBs + memory files and push to a GitHub backup repo.

    Returns a short summary string. Never raises — errors are logged and
    returned as strings.
    """
    if not backup_repo:
        log.info("backup_disabled", reason="no backup_repo configured")
        return "Backup disabled — no backup_repo configured."

    try:
        return await _do_backup(
            data_dir=data_dir,
            memory_dir=memory_dir,
            backup_repo=backup_repo,
        )
    except Exception as exc:
        log.error("backup_failed", error=str(exc))
        return f"Backup error: {exc}"


async def _do_backup(
    *,
    data_dir: Path,
    memory_dir: Path,
    backup_repo: str,
) -> str:
    work = Path(tempfile.mkdtemp(prefix="enki-backup-"))
    dump_dir = work / "dumps"
    dump_dir.mkdir()

    try:
        # 1. Dump SQLite DBs
        dumped = 0
        for name in DB_NAMES:
            db_path = data_dir / name
            if not db_path.exists():
                continue
            sql_name = name.replace(".db", ".sql")
            out_file = dump_dir / sql_name
            rc, stdout, stderr = await _run_cmd(
                "sqlite3",
                str(db_path),
                ".dump",
            )
            if rc != 0:
                log.warning(
                    "sqlite_dump_failed",
                    db=name,
                    rc=rc,
                    stderr=stderr.decode(errors="replace"),
                )
                continue
            out_file.write_bytes(stdout)
            dumped += 1

        # 2. Copy entire memory directory
        memory_files = 0
        if memory_dir.is_dir():
            mem_dst = dump_dir / "memory"
            shutil.copytree(str(memory_dir), str(mem_dst))
            memory_files = sum(1 for f in mem_dst.rglob("*") if f.is_file())

        # 3. Clone backup repo (shallow)
        clone_dir = work / "repo"
        rc, _, stderr = await _run_cmd(
            "gh",
            "repo",
            "clone",
            backup_repo,
            str(clone_dir),
            "--",
            "--depth=1",
        )
        if rc != 0:
            msg = f"Failed to clone {backup_repo}: {stderr.decode(errors='replace')}"
            log.error("backup_clone_failed", error=msg)
            return f"Backup error: {msg}"

        # 4. Copy dumps into clone
        for item in dump_dir.iterdir():
            dest = clone_dir / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(str(dest))
                shutil.copytree(str(item), str(dest))
            else:
                shutil.copy2(str(item), str(dest))

        # 5. git add + commit + push
        await _run_cmd("git", "add", ".", cwd=clone_dir)

        iso_date = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rc, _, stderr = await _run_cmd(
            "git",
            "commit",
            "-m",
            f"backup {iso_date}",
            cwd=clone_dir,
        )
        if rc != 0:
            stderr_str = stderr.decode(errors="replace")
            if "nothing to commit" in stderr_str:
                log.info("backup_nothing_changed")
                return f"Backup: nothing changed. {dumped} DBs dumped, {memory_files} memory files — no new data to push."
            msg = f"git commit failed: {stderr_str}"
            log.error("backup_commit_failed", error=msg)
            return f"Backup error: {msg}"

        rc, _, stderr = await _run_cmd("git", "push", cwd=clone_dir)
        if rc != 0:
            msg = f"git push failed: {stderr.decode(errors='replace')}"
            log.error("backup_push_failed", error=msg)
            return f"Backup error: {msg}"

        summary = f"Backup complete: {dumped} DBs, {memory_files} memory files pushed to {backup_repo}"
        log.info("backup_complete", dumped=dumped, memory_files=memory_files)
        return summary

    finally:
        shutil.rmtree(str(work), ignore_errors=True)
