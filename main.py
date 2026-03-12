"""Enki — entry point."""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import MutableMapping
from datetime import UTC
from pathlib import Path
from typing import Any, NamedTuple

import click
import structlog


class _SpinnerClearProcessor:
    """Clear the braille spinner line before structlog writes, preventing visual bleed."""

    def __call__(self, logger: Any, method: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        try:
            import src.interfaces.cli as _cli

            if _cli.is_spinner_active():
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
        except ImportError:
            pass
        return event_dict


structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.set_exc_info,
        _SpinnerClearProcessor(),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()

_PID_FILE = Path("data/telegram.pid")


def _pid_is_our_process(pid: int) -> bool:
    """On Linux, verify the PID belongs to our main.py (not a recycled container PID)."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
        return "main.py" in cmdline
    except OSError:
        return False


def _acquire_pid_lock() -> None:
    """Ensure only one Telegram bot instance runs. Exits if another is alive."""
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _PID_FILE.exists():
        try:
            existing_pid = int(_PID_FILE.read_text().strip())
            os.kill(existing_pid, 0)  # raises OSError if process is dead
            if _pid_is_our_process(existing_pid) and existing_pid != os.getpid():
                print(f"ERROR: Telegram bot already running (PID {existing_pid}). Exiting.", flush=True)
                raise SystemExit(1)
            # PID exists but it's not our process, or it's our own PID (recycled) — overwrite
        except (OSError, ValueError):
            pass  # stale PID file — overwrite
    _PID_FILE.write_text(str(os.getpid()))


def _release_pid_lock() -> None:
    with contextlib.suppress(OSError):
        _PID_FILE.unlink(missing_ok=True)


class BuildResult(NamedTuple):
    agent: Any
    config: Any
    compactor: Any
    schedule_store: Any
    manage_schedule_tool: Any
    job_registry: Any
    audit: Any


def _build_agent(notifier: Any = None) -> BuildResult:
    """Wire up all dependencies and return a BuildResult."""
    import uuid
    from pathlib import Path

    import anthropic

    from src.agent import Agent
    from src.audit.db import AuditDB
    from src.config import Settings
    from src.guardrails import GuardrailChain
    from src.guardrails.allowlist import AllowlistHook
    from src.guardrails.audit_hook import AuditHook
    from src.guardrails.confirmation_gate import ConfirmationGateHook
    from src.guardrails.cost_guard import CostGuardHook
    from src.guardrails.loop_detector import LoopDetectorHook
    from src.guardrails.rate_limiter import RateLimiterHook
    from src.guardrails.scope_check import ScopeCheckHook
    from src.jobs import JobRegistry
    from src.memory.compactor import MemoryCompactor
    from src.memory.store import MemoryStore
    from src.pipeline.store import PipelineStore
    from src.schedule.store import ScheduleStore
    from src.teams.store import TeamsStore
    from src.teams.templates import seed_engineering_teams
    from src.tools import register, registry
    from src.tools.calendar_read import CalendarReadTool
    from src.tools.claude_code import RunClaudeCodeTool
    from src.tools.email_read import EmailReadTool
    from src.tools.evolve import ProposeTool
    from src.tools.github_tools import (
        CreatePRTool,
        GitCommitTool,
        GitDiffTool,
        GitPushBranchTool,
        GitStatusTool,
    )
    from src.tools.job_status import JobStatusTool
    from src.tools.loader import load_tools_from_dir
    from src.tools.manage_pipeline import ManagePipelineTool
    from src.tools.manage_schedule import ListScheduleTool, ManageScheduleTool
    from src.tools.manage_team import ManageTeamTool
    from src.tools.manage_workspace import ListWorkspacesTool, ManageWorkspaceTool
    from src.tools.notes import NotesTool
    from src.tools.remember import ForgetTool, RememberTool
    from src.tools.remove_tool import RemoveToolTool
    from src.tools.restart import RequestRestartTool
    from src.tools.run_pipeline import RunPipelineTool
    from src.tools.save_pipeline_artifact import SavePipelineArtifactTool
    from src.tools.send_message import SendMessageTool
    from src.tools.spawn_agent import SpawnAgentTool
    from src.tools.spawn_team import SpawnTeamTool
    from src.tools.tasks import TasksTool
    from src.tools.team_report import TeamReportTool
    from src.tools.web_search import WebSearchTool
    from src.workspaces.store import WorkspaceStore

    config = Settings()  # type: ignore[call-arg]  # reads .env, fails fast on missing keys

    data_dir = config.audit_db_path.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    config.memory_db_path.parent.mkdir(parents=True, exist_ok=True)

    audit = AuditDB(config.audit_db_path)
    audit.purge_old_tier2(30)  # M-07: clean stale Tier2 records at startup
    _facts_path = Path("memory/facts.md")
    _logs_dir = Path("memory/logs")
    memory = MemoryStore(config.memory_db_path, logs_dir=_logs_dir, facts_path=_facts_path)

    class _CliNotifier:
        async def ask_confirm(self, tool_name: str, params: dict) -> bool:  # type: ignore[type-arg]
            return click.confirm(f"Allow {tool_name}?", default=False)

        async def send_diff(self, tool_name: str, description: str, code: str, code_hash: str) -> None:
            click.echo(f"\n=== Proposed tool: {tool_name} ===")
            click.echo(f"Description: {description}")
            click.echo(f"SHA256: {code_hash[:16]}")
            click.echo("--- code ---")
            click.echo(code)
            click.echo("--- end ---")

        async def wait_for_approval(self, tool_name: str) -> bool:
            return click.confirm(f"Approve tool '{tool_name}'?", default=False)

        async def send(self, message: str) -> None:
            click.echo(message)

        async def ask_single_confirm(self, reason: str, changes_summary: str) -> bool:
            click.echo(f"\nReason: {reason}")
            click.echo(f"Details: {changes_summary}")
            return click.confirm("Proceed?", default=False)

        async def ask_double_confirm(self, reason: str, changes_summary: str) -> bool:
            click.echo(f"\nConfirmation required. Reason: {reason}")
            click.echo(f"Details: {changes_summary}")
            if not click.confirm("Confirm? (1/2)", default=False):
                return False
            return click.confirm("Are you sure? (2/2)", default=False)

        async def ask_free_text(self, prompt: str, timeout_s: int = 300) -> str | None:
            click.echo(f"\n{prompt}")
            import asyncio

            from src.interfaces.cli import _prompt_async

            asyncio.get_event_loop()
            try:
                return await asyncio.wait_for(_prompt_async("Your answer: "), timeout=timeout_s)
            except TimeoutError:
                return None

        async def ask_scope_approval(self, prompt: str, timeout_s: int = 600) -> str | None:
            click.echo(f"\n{prompt}")
            click.echo("[a]pprove / [r]eject / [v] revise")
            import asyncio

            from src.interfaces.cli import _prompt_async

            try:
                answer = await asyncio.wait_for(_prompt_async("Choice: "), timeout=timeout_s)
            except TimeoutError:
                return None
            if answer and answer.strip().lower().startswith("a"):
                return "approve"
            if answer and answer.strip().lower().startswith("r"):
                return "reject"
            # Revise — ask for feedback
            try:
                return await asyncio.wait_for(_prompt_async("Feedback: "), timeout=timeout_s)
            except TimeoutError:
                return None

    _notifier_instance = notifier if notifier is not None else _CliNotifier()

    # Cost guard — created early so tools can report sub-agent costs
    cost_guard = CostGuardHook(
        max_tokens_per_session=config.max_tokens_per_session,
        max_daily_cost_usd=config.max_daily_cost_usd,
        max_monthly_cost_usd=config.max_monthly_cost_usd,
        max_llm_calls_per_session=config.max_llm_calls_per_session,
        max_autonomous_turns=config.max_autonomous_turns,
        notifier=_notifier_instance,
    )

    # Register all tools
    register(RememberTool(facts_path=_facts_path))
    register(ForgetTool(facts_path=_facts_path))
    register(TasksTool(config.tasks_db_path))
    register(WebSearchTool(config.brave_search_api_key))
    register(NotesTool(data_dir / "projects"))
    register(CalendarReadTool())
    if config.imap_host and config.imap_user and config.imap_password:
        register(EmailReadTool(config.imap_host, config.imap_user, config.imap_password))
    tools_dir = Path(__file__).parent / "src" / "tools"
    pending_dir = Path(__file__).parent / "tools_pending"
    disabled_dir = Path(__file__).parent / "tools_disabled"
    register(ProposeTool(pending_dir=pending_dir, tools_dir=tools_dir, notifier=_notifier_instance))
    register(RemoveToolTool(tools_dir=tools_dir, disabled_dir=disabled_dir, registry=registry))
    register(RequestRestartTool(notifier=_notifier_instance, cooldown_seconds=config.restart_cooldown_seconds))
    register(SendMessageTool(notifier=_notifier_instance))
    register(SpawnAgentTool(config=config, tool_registry=registry, cost_guard=cost_guard))

    # Job registry — in-memory, tracks live background tasks (CCC, pipelines, teams)
    job_registry = JobRegistry()
    register(JobStatusTool(registry=job_registry))

    # Shared Anthropic client — used by tools for stateless summarization (no conversation pollution)
    _anthropic_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    teams_db_path = data_dir / "teams.db"
    teams_store = TeamsStore(teams_db_path)
    seed_engineering_teams(teams_store)
    _spawn_team_tool = SpawnTeamTool(
        store=teams_store,
        config=config,
        tool_registry=registry,
        notifier=_notifier_instance,
        job_registry=job_registry,
        cost_guard=cost_guard,
        anthropic_client=_anthropic_client,
        summary_model=config.haiku_model,
    )
    register(_spawn_team_tool)
    register(TeamReportTool(store=teams_store))
    register(ManageTeamTool(store=teams_store))

    # Workspace registry — built before git tools and RunClaudeCodeTool
    workspace_db_path = data_dir / "workspaces.db"
    workspace_store = WorkspaceStore(workspace_db_path)
    register(ListWorkspacesTool(store=workspace_store, workspaces_base_dir=config.workspaces_base_dir))
    register(ManageWorkspaceTool(store=workspace_store, workspaces_base_dir=config.workspaces_base_dir))
    _run_claude_code_tool = RunClaudeCodeTool(
        notifier=_notifier_instance,
        project_dir=Path(__file__).parent,
        workspace_store=workspace_store,
        job_registry=job_registry,
        timeout_seconds=config.claude_code_timeout_seconds,
        cooldown_seconds=config.claude_code_cooldown_seconds,
        anthropic_client=_anthropic_client,
        summary_model=config.haiku_model,
    )
    register(_run_claude_code_tool)

    # Git/GitHub tools — workspace-aware (workspace_id routes cwd to external workspace)
    register(GitStatusTool(workspace_store=workspace_store))
    register(GitDiffTool(workspace_store=workspace_store))
    register(GitCommitTool(workspace_store=workspace_store))
    register(GitPushBranchTool(workspace_store=workspace_store))
    register(CreatePRTool(workspace_store=workspace_store))

    # Pipeline
    pipeline_db_path = data_dir / "pipelines.db"
    pipeline_store = PipelineStore(pipeline_db_path)
    register(ManagePipelineTool(pipeline_store=pipeline_store, workspace_store=workspace_store, job_registry=job_registry))
    register(SavePipelineArtifactTool(pipeline_store=pipeline_store))

    from src.tools.pipeline_status import PipelineStatusTool

    register(PipelineStatusTool(pipeline_store=pipeline_store))
    _run_pipeline_tool = RunPipelineTool(
        notifier=_notifier_instance,
        pipeline_store=pipeline_store,
        workspace_store=workspace_store,
        teams_store=teams_store,
        config=config,
        tool_registry=registry,
        job_registry=job_registry,
        cost_guard=cost_guard,
        anthropic_client=_anthropic_client,
        summary_model=config.haiku_model,
    )
    register(_run_pipeline_tool)

    # Schedule store + tools (scheduler wired later by each command)
    schedule_db_path = data_dir / "schedule.db"
    schedule_store = ScheduleStore(schedule_db_path)
    _manage_schedule_tool = ManageScheduleTool(store=schedule_store)
    register(ListScheduleTool(store=schedule_store))
    register(_manage_schedule_tool)

    # Auto-discover any tools written by propose_tool (no-arg constructors only)
    load_tools_from_dir(tools_dir)

    loop_detector = LoopDetectorHook(threshold=config.loop_detection_threshold)
    rate_limiter = RateLimiterHook(max_per_turn=config.max_tool_calls_per_turn)

    session_id = str(uuid.uuid4())
    audit_hook = AuditHook(audit, session_id=session_id)

    chain = GuardrailChain(
        [
            AllowlistHook(registry),
            ScopeCheckHook(),
            loop_detector,
            rate_limiter,
            cost_guard,
            ConfirmationGateHook(_notifier_instance),
            audit_hook,
        ]
    )

    agent = Agent(
        config=config,
        guardrails=chain,
        memory=memory,
        tool_registry=registry,
        audit=audit,
        cost_guard=cost_guard,
        loop_detector=loop_detector,
        rate_limiter=rate_limiter,
        session_id=session_id,
    )

    compactor = MemoryCompactor(
        store=memory,
        anthropic_client=_anthropic_client,
        facts_path=_facts_path,
        model=config.haiku_model,
    )
    # Wire compactor into agent so idle-timeout resets trigger fact distillation
    agent.set_compactor(compactor)

    return BuildResult(
        agent=agent,
        config=config,
        compactor=compactor,
        schedule_store=schedule_store,
        manage_schedule_tool=_manage_schedule_tool,
        job_registry=job_registry,
        audit=audit,
    )


@click.group()
def cli() -> None:
    pass


@cli.command()
def chat() -> None:
    """Start interactive CLI session."""
    from src.interfaces.cli import run_cli

    result = _build_agent()
    run_cli(result.agent, compactor=result.compactor)


@cli.command()
def telegram() -> None:
    """Start Telegram bot."""
    from src.config import Settings
    from src.interfaces.telegram_bot import TelegramBot
    from src.scheduler import Scheduler, default_jobs

    config = Settings()  # type: ignore[call-arg]

    bot = TelegramBot(config.telegram_bot_token, config.telegram_chat_id, confirm_timeout=config.confirm_timeout_seconds)
    result = _build_agent(notifier=bot)
    agent = result.agent
    compactor = result.compactor
    schedule_store = result.schedule_store
    manage_schedule_tool = result.manage_schedule_tool
    job_registry = result.job_registry
    bot.set_agent(agent)
    bot.set_job_registry(job_registry)

    from src.audit.query import AuditQuery

    bot.set_audit_query(AuditQuery(db=result.audit))

    # Seed default jobs on first run, then load all from store
    from src.schedule.store import ScheduleStore as _SS

    assert isinstance(schedule_store, _SS)
    if not schedule_store.list_all():
        for job in default_jobs():
            schedule_store.upsert(job.job_id, job.cron, job.prompt)

    scheduler = Scheduler(agent=agent, notifier=bot, store=schedule_store, timezone=config.timezone)
    scheduler.load_from_store()

    # Wire scheduler into manage_schedule tool
    from src.tools.manage_schedule import ManageScheduleTool as _MST

    assert isinstance(manage_schedule_tool, _MST)
    manage_schedule_tool.set_scheduler(scheduler)

    _LAST_SEEN_FILE = Path("data/last_seen")

    async def _heartbeat_writer() -> None:
        """Write current timestamp to data/last_seen every 60 s."""
        import asyncio

        while True:
            try:
                _LAST_SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
                _LAST_SEEN_FILE.write_text(str(int(__import__("time").time())))
            except Exception as exc:
                log.warning("heartbeat_write_failed", error=str(exc))
            await asyncio.sleep(60)

    async def _connectivity_monitor() -> None:
        """Ping 8.8.8.8:53 every 60 s; notify when internet is restored after an outage."""
        import asyncio
        import socket
        import time

        offline_since: float | None = None
        while True:
            online = False
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.settimeout(config.connectivity_timeout_seconds)
                    sock.connect(("8.8.8.8", 53))
                    online = True
                finally:
                    sock.close()
            except OSError:
                pass

            now = time.time()
            if online:
                if offline_since is not None:
                    duration_min = int((now - offline_since) / 60)
                    from datetime import datetime

                    lost_at = datetime.fromtimestamp(offline_since, tz=UTC).strftime("%H:%M UTC")
                    with contextlib.suppress(Exception):
                        await bot.send(f"Internet restored. Was offline from {lost_at} ({duration_min} min ago).")
                    offline_since = None
            else:
                if offline_since is None:
                    offline_since = now
                    log.warning("connectivity_lost")

            await asyncio.sleep(60)

    async def _system_health_monitor() -> None:
        """Run system health checks every 30 min; route problems through the Agent."""
        import asyncio

        from src.monitoring.system_monitor import SystemMonitor

        data_dir = config.audit_db_path.parent
        monitor = SystemMonitor(data_dir=data_dir)

        while True:
            await asyncio.sleep(1800)  # 30 minutes
            try:
                alerts = monitor.run_checks()
                msg = SystemMonitor.format_alerts(alerts)
                if msg:
                    log.warning("system_health_alerts", count=len(alerts))
                    response = await agent.run_turn(f"SYSTEM: {msg}\n\nInform the user about these issues and suggest actions.")
                    await bot.send(response)
            except Exception as exc:
                log.warning("system_health_monitor_failed", error=str(exc))

    async def _on_startup(_app: object) -> None:
        import asyncio
        import time

        from telegram import BotCommand, MenuButtonCommands
        from telegram.ext import Application as _App

        assert isinstance(_app, _App)
        await _app.bot.set_my_commands(
            [
                BotCommand("start", "Check Enki is alive"),
                BotCommand("newsession", "Clear conversation history, start fresh"),
                BotCommand("cost", "Session token and cost usage"),
                BotCommand("audit", "Last 5 security events"),
                BotCommand("help", "List available tools and commands"),
                BotCommand("status", "Show running background jobs"),
                BotCommand("memory", "Search or list stored facts"),
            ]
        )
        await _app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        scheduler.start()

        # Startup catch-up: detect downtime and route awareness through the Agent
        try:
            if _LAST_SEEN_FILE.exists():
                last_ts = int(_LAST_SEEN_FILE.read_text().strip())
                gap = int(time.time()) - last_ts
                if gap > 120:
                    from datetime import datetime

                    last_str = datetime.fromtimestamp(last_ts, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
                    now_str = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

                    # Calculate missed jobs during the downtime window
                    missed = scheduler.calculate_missed_jobs(since=last_ts)
                    missed_section = ""
                    if missed:
                        missed_lines = []
                        for m in missed:
                            fire_str = datetime.fromtimestamp(m.expected_time, tz=UTC).strftime("%H:%M UTC")
                            missed_lines.append(f"  - {fire_str} {m.job_id}")
                        missed_section = "\n\nMissed scheduled jobs:\n" + "\n".join(missed_lines)

                    # Route through Agent so Enki is genuinely aware of its own downtime
                    downtime_msg = (
                        f"SYSTEM: You were down from {last_str} to {now_str} ({gap // 60} minutes). "
                        f"You just restarted.{missed_section}\n\n"
                        f"Inform the user about the downtime. "
                        f"If there are missed jobs, ask if they want you to run them now."
                    )
                    response = await agent.run_turn(downtime_msg)
                    await bot.send(response)
                    log.info(
                        "downtime_awareness_routed",
                        gap_minutes=gap // 60,
                        missed_jobs=len(missed),
                    )
        except Exception as exc:
            log.warning("startup_catchup_failed", error=str(exc))

        # Background tasks
        asyncio.create_task(_heartbeat_writer())
        asyncio.create_task(_connectivity_monitor())
        asyncio.create_task(_system_health_monitor())

        # Weekly facts cleanup — runs only if due and facts.md is large enough
        try:
            await asyncio.wait_for(compactor.clean_facts(), timeout=30.0)
        except Exception as exc:
            log.warning("facts_cleanup_startup_failed", error=str(exc))

    async def _on_shutdown(_app: object) -> None:
        import asyncio

        scheduler.stop()
        cancelled = job_registry.cancel_all()
        if cancelled:
            log.info("shutdown_cancelled_jobs", count=cancelled)

        # Cancel any tracked agent background tasks (e.g. compaction)
        for task in list(agent._background_tasks):
            task.cancel()
        if agent._background_tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*agent._background_tasks, return_exceptions=True)

        try:
            await asyncio.wait_for(
                compactor.compact_session(agent.session_id),
                timeout=30.0,
            )
        except TimeoutError:
            log.warning("compaction_timeout")
        except Exception as exc:
            log.warning("compaction_failed", error=str(exc))

        # Cancel remaining fire-and-forget tasks (heartbeat, connectivity, health monitor)
        current = asyncio.current_task()
        for task in asyncio.all_tasks():
            if task is not current and not task.done():
                task.cancel()
        log.info("shutdown_tasks_cancelled")

    bot.set_post_init(_on_startup)
    bot.set_post_shutdown(_on_shutdown)
    _acquire_pid_lock()
    try:
        bot.run()
    finally:
        _release_pid_lock()


if __name__ == "__main__":
    cli()
