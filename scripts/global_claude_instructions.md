# Global Claude Code Instructions

These rules apply to every Claude Code invocation regardless of project.

---

## Universal Rules (all languages)

- **TDD always**: write failing tests first, then implement to pass, then refactor.
  Never write implementation before the test exists.
- **Small commits**: commit after each logical unit of work, not at the end.
- **Read before writing**: understand existing code before modifying it.
  Never guess at interfaces — check the actual types and signatures.
- **No over-engineering**: implement exactly what is asked. Do not add features,
  abstractions, or configurability that wasn't requested.
- **Meaningful names**: variables, functions, and files should be self-documenting.
  A comment should explain *why*, not *what*.
- **No magic numbers**: extract constants with descriptive names.
- **Fail fast**: validate at boundaries (user input, external APIs).
  Trust internal code — don't add defensive checks for impossible states.
- **Delete dead code**: don't comment it out, don't keep it "just in case".

---

## Python Projects

- Python 3.12+, full type hints on all functions and classes.
- `async def` / `await` throughout — no blocking calls in async context.
- `structlog` for all logging — never `print()`, never `logging`.
- Protocol-based interfaces (not ABCs) for extensibility.
- No global singletons except `config`. Inject everything at startup.
- `ruff` for linting + formatting. `mypy --strict` for type checking.
- `pytest` + `pytest-asyncio` for tests.
- No `subprocess`, `eval`, `exec`, `os.system` except where explicitly documented.

## TypeScript / JavaScript Projects

- Strict TypeScript (`"strict": true` in tsconfig). No `any` unless justified.
- ESLint + Prettier. Follow existing config — don't change tooling unless asked.
- Vitest or Jest for tests — check which is already set up before choosing.
- Async/await over callbacks and raw Promises.
- No `console.log` in committed code — use proper logging.
- Export types explicitly. Prefer named exports over default exports.

## Go Projects

- `gofmt` and `go vet` must pass.
- `go test ./...` for tests — table-driven tests preferred.
- Errors returned, not panicked. Wrap with `fmt.Errorf("context: %w", err)`.
- Interfaces defined at point of use (consumer, not producer).
- No global state. Pass dependencies explicitly.

## Other Languages

- Follow the conventions already established in the codebase.
- Check for existing linting/formatting configs and use them.
- Run the existing test suite before declaring done — it must pass.

---

## Git Discipline

- Branch names: `feat/description`, `fix/description`, `chore/description`.
- Commit messages: imperative mood, present tense ("add X", not "added X").
- Never commit directly to `main` or `master`.
- Never force-push.
- Run tests before committing — don't push a broken build.

---

## When Stuck

1. Read the existing code more carefully. The answer is usually there.
2. Check the project's existing tests for usage examples.
3. Search the web for the specific error or API.
4. If genuinely blocked, stop and explain the blocker clearly — don't guess.
