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

## Refactoring safety (non-negotiable)
- **Before renaming ANY function, variable, class, or module-level symbol**: grep the ENTIRE codebase for all references (including `main.py`, tests, and config files). Update every reference in the same commit. A rename that misses a reference is a production crash.
- **After any refactor**: run `docker-compose build enki && docker-compose run --rm enki python -c "from main import _build_agent"` to verify the app can start. Unit tests are NOT sufficient — they mock too much.
- **Never change a public interface** (function signature, module-level name, class attribute) **without grepping for all callers first**. Use: `grep -r "old_name" src/ main.py tests/`
- **`main.py` is part of the codebase**. It is NOT just a script. It is covered by mypy (`mypy src/ main.py`), and changes to `src/` that affect names used in `main.py` MUST update `main.py` too.
- **Pre-commit runs**: `ruff check`, `mypy src/ main.py`, `pytest`. All three must pass.
