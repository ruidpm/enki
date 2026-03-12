# Architecture Boundaries — Assessment & Remediation Plan

**Date**: 2026-03-12
**Status**: Uncommitted — review before implementing

---

## Summary

Codebase audit identified boundary violations across the Enki project.
No circular imports exist, but several modules violate logical layering
and use fragmented protocol definitions.

---

## HIGH — Fix Required

### 1. TelegramBot reaches into Agent privates

**Files**: `src/interfaces/telegram_bot.py:82-84, 102`

```python
daily = self._agent._cost_guard.daily_cost_usd    # private!
monthly = self._agent._cost_guard.monthly_cost_usd
tokens = self._agent._cost_guard.session_tokens
q = AuditQuery(self._agent._audit)                # private!
```

**Fix**: Add public properties to Agent:
- `agent.daily_cost_usd`
- `agent.monthly_cost_usd`
- `agent.session_tokens`
- `agent.audit` (read-only property)

### 2. Five different Notifier protocols scattered across tools

**Files**:
- `src/interfaces/notifier.py:12-18` — canonical (incomplete)
- `src/guardrails/confirmation_gate.py:26-29` — `ask_confirm` only
- `src/tools/spawn_team.py:30-31` — `send` only
- `src/tools/run_pipeline.py:76-79` — `ask_single_confirm`, `send`, `ask_free_text`
- `src/tools/claude_code.py:110-112` — `ask_single_confirm`, `send`

**Fix**: Extend canonical `src/interfaces/notifier.py` with ALL methods:
```python
class Notifier(Protocol):
    async def send(self, message: str) -> None: ...
    async def ask_confirm(self, tool_name: str, params: dict[str, Any]) -> bool: ...
    async def ask_single_confirm(self, reason: str, changes_summary: str) -> bool: ...
    async def ask_double_confirm(self, reason: str, changes_summary: str) -> bool: ...
    async def ask_free_text(self, prompt: str, timeout_s: int = 300) -> str | None: ...
    async def send_diff(self, tool_name: str, description: str, code: str, code_hash: str) -> None: ...
    async def wait_for_approval(self, tool_name: str) -> bool: ...
```
Then delete all local Notifier definitions from tools and import from `src/interfaces/notifier`.

---

## MEDIUM — Worth Doing

### 3. Three different Agent protocols / `Any` usage

**Files**:
- `src/tools/spawn_team.py:34-35` — local `Agent(Protocol)` with `run_turn`
- `src/tools/run_pipeline.py:148` — `self._agent: Any = None`
- `src/tools/claude_code.py:157` — `self._agent: Any = None`

**Fix**: Create `src/interfaces/agent_protocol.py`:
```python
class AgentProtocol(Protocol):
    async def run_turn(self, user_message: str) -> str: ...
```
Import in all three tools. Name it `AgentProtocol` to avoid collision with `src/agent.Agent`.

### 4. Tools import backwards from guardrails

**Files**:
- `src/tools/spawn_team.py:17` — `from src.guardrails.confirmation_gate import REQUIRES_CONFIRM`
- `src/tools/run_pipeline.py:26-27` — same + `CostGuardHook`
- `src/tools/spawn_agent.py:10-11` — same

**Expected**: `agent → guardrails → tools` (guardrails control tools)
**Actual**: `tools → guardrails` (reverse dependency)

**Fix**: Move `REQUIRES_CONFIRM` frozenset to `src/constants.py`. Tools and guardrails both import from constants.

---

## LOW — Fine for Now

### 5. GuardrailHook protocol not `@runtime_checkable`
- `src/guardrails/__init__.py:12` — no `isinstance()` checks needed currently

### 6. AuditQuery accesses AuditDB._conn()
- `src/audit/query.py:36, 42, 57` — intentional, query is the public interface over the DB

### 7. CLI module-level SpinnerState
- `src/interfaces/cli.py:28` — encapsulated, not shared cross-module

---

## Clean Boundaries (no violations)

- All store modules (teams, pipeline, workspaces, schedule, memory) — no imports from tools
- Agent does not import from tools directly
- No circular imports detected
- No global singletons beyond `config`

---

## CI/CD: Bandit False Positives

Bandit flags parameterized SQL as injection vectors because it sees f-strings
containing SQL keywords. These need `# nosec B608` annotations:

### True false positives (parameterized queries — safe):
- `src/audit/query.py:37` — uses `?` placeholders, f-string only builds WHERE clause from safe literals
- `src/audit/query.py:58` — same pattern

### Already mitigated (input validation + escaping):
- `src/tools/tasks.py:120` — status_filter validated against whitelist
- `src/tools/tasks.py:137` — title/notes escaped via `_escape_sql_string()`
- `src/tools/tasks.py:165` — task_id validated as int via `_validate_int_id()`
- `src/tools/tasks.py:174` — task_id validated as int

**Action**: Add `# nosec B608` to these lines, or configure `.bandit` excludes.

---

## Implementation Order

1. **Notifier protocol consolidation** (high impact, low risk)
2. **Agent public properties** (high impact, medium risk — touches Agent)
3. **AgentProtocol extraction** (medium impact, low risk)
4. **REQUIRES_CONFIRM → constants.py** (medium impact, low risk)
5. **Bandit nosec annotations** (CI fix, no logic change)
