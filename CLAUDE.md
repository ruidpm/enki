# Enki — Build Context

## Project
Enki: Python 3.12 AI personal assistant with Telegram + CLI interfaces.

## Rules (non-negotiable)
- Python 3.12, full type hints on everything
- Protocol-based interfaces (NOT ABCs)
- `async def` / `await` throughout
- `structlog` for all logging (never `print`, never `logging`)
- No `subprocess`, `eval`, `exec` anywhere except ONE call in `src/tools/restart.py`
- No global singletons except `config`
- Dependency injection at startup — everything injected, nothing imported globally
