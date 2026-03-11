"""Interactive CLI interface."""
from __future__ import annotations

import asyncio
import contextlib
import sys
from typing import TYPE_CHECKING

import click
import structlog

if TYPE_CHECKING:
    from src.agent import Agent
    from src.memory.compactor import MemoryCompactor

log = structlog.get_logger()

_SPINNER_FRAMES = ["*", "o", "O", "@", "O", "o"]


class SpinnerState:
    """Encapsulates spinner state instead of using a module-level global."""

    active: bool = False


_spinner_state = SpinnerState()


def is_spinner_active() -> bool:
    """Check whether the CLI spinner is currently animating."""
    return _spinner_state.active


async def _spinner(stop: asyncio.Event) -> None:
    """Animate a braille spinner on stdout until stop is set."""
    _spinner_state.active = True
    i = 0
    while not stop.is_set():
        sys.stdout.write(f"\r{_SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]} thinking...")
        sys.stdout.flush()
        i += 1
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(stop.wait()), timeout=0.1)
    _spinner_state.active = False
    sys.stdout.write("\r" + " " * 20 + "\r")
    sys.stdout.flush()


async def _prompt_async(prompt: str) -> str:
    """Read a line of input without blocking the event loop.

    Runs the blocking input() call in a thread pool so background asyncio
    tasks (e.g. CCC job completion notifications) can fire while waiting.
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, lambda: input(prompt))
    except EOFError:
        raise


def run_cli(agent: Agent, compactor: MemoryCompactor | None = None) -> None:
    """Start the interactive CLI REPL."""
    click.echo("Enki ready. Type 'exit' or Ctrl-C to quit.\n")

    async def _loop() -> None:
        try:
            while True:
                try:
                    user_input = await _prompt_async("You> ")
                except (EOFError, KeyboardInterrupt):
                    click.echo("\nGoodbye.")
                    break

                if user_input.strip().lower() in ("exit", "quit", "q"):
                    click.echo("Goodbye.")
                    break
                if not user_input.strip():
                    continue

                try:
                    stop = asyncio.Event()
                    spinner_task = asyncio.create_task(_spinner(stop))
                    try:
                        response = await agent.run_turn(user_input)
                    finally:
                        stop.set()
                        await spinner_task
                    click.echo(f"\nA: {response}\n")
                except Exception as exc:
                    log.error("cli_error", error=str(exc))
                    click.echo(f"[Error: {exc}]\n")
        finally:
            if compactor is not None:
                try:
                    await compactor.compact_session(agent.session_id)
                except Exception as exc:
                    log.warning("compaction_failed", error=str(exc))

    asyncio.run(_loop())
