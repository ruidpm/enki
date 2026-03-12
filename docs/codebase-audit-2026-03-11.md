# Enki Codebase Audit — 2026-03-11

**Review team:** Architect, LLM/AI Agent Specialist, Backend Engineer, Security Engineer, QA Engineer, DevOps Engineer, Code Quality Specialist

**Scope:** Full codebase review of `/Users/rui/Desktop/projects/personal-assistant/`

**Methodology:** Each specialist independently read the entire `src/`, `tests/`, and infrastructure files, then produced findings. This report merges and deduplicates all findings, organized by severity.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Critical Findings](#critical-findings)
3. [High-Severity Findings](#high-severity-findings)
4. [Medium-Severity Findings](#medium-severity-findings)
5. [Low-Severity Findings](#low-severity-findings)
6. [Positive Observations](#positive-observations)
7. [Prioritized Action Plan](#prioritized-action-plan)
8. [Appendix: Full Finding Index](#appendix-full-finding-index)

---

## Executive Summary

Enki has a **strong architectural foundation**: clean layering, Protocol-based interfaces, comprehensive guardrail chain, tiered audit logging, and well-structured async patterns. The codebase follows its own rules (CLAUDE.md) with few exceptions and demonstrates security-conscious design.

However, the review identified **11 critical**, **14 high**, **24 medium**, and **15+ low** severity findings across security, architecture, backend patterns, testing, and operations. The most urgent issues are:

1. **Exposed credentials** in committed `.env` file (rotate immediately)
2. **SQL injection** in `tasks.py` via string concatenation
3. **Unbounded conversation context** with no sliding window
4. **Sub-agent cost/guardrail bypass** — costs not aggregated, confirmation gates skipped
5. **SQLite race conditions** in stores using `check_same_thread=False` without locks
6. **Major test coverage gaps** — 9+ modules with zero tests

**No code changes are included in this report.** This is analysis only.

---

## Critical Findings

### C-01: Exposed Credentials in `.env` File
**Reporters:** Security, DevOps | **File:** `.env`

Real API keys are committed to the repository:
- `ANTHROPIC_API_KEY=sk-ant-api03-...`
- `TELEGRAM_BOT_TOKEN=8640790599:...`
- `BRAVE_SEARCH_API_KEY=BSA2cMAnCTh9...`
- `GH_TOKEN=ghp_KRCiQ8mxXfTpSSXNx...`
- `TELEGRAM_CHAT_ID=5307900668`

**Impact:** Anyone with repo access can hijack the Telegram bot, incur API costs, access GitHub repos, and exfiltrate data.

**Remediation:**
1. Rotate ALL credentials immediately
2. `git rm --cached .env` and rewrite history
3. Verify `.gitignore` entry is effective going forward

---

### C-02: SQL Injection in `tasks.py`
**Reporters:** Security, Code Quality, Backend | **File:** `src/tools/tasks.py:74-117`

Status filter and all CRUD fields use f-string SQL construction:

```python
f"FROM tasks WHERE status = '{status_filter}' ORDER BY ..."
f"INSERT INTO tasks (title, notes, due_date) VALUES ('{title}', '{notes}', {due_val});"
f"UPDATE tasks SET {', '.join(fields)} WHERE id = {task_id};"
```

The `chr(39)` escaping on lines 98/100 is fragile and non-standard. While the subprocess isolation provides some mitigation (sqlite3 CLI, not Python module), this violates safe coding practices.

**Impact:** Agent-directed SQL injection could exfiltrate or destroy task data.

**Remediation:** Use parameterized queries throughout. If using sqlite3 CLI, validate/whitelist all inputs.

---

### C-03: Unbounded Conversation Context
**Reporters:** LLM/AI, Backend | **File:** `src/agent.py:95,201,240,245,313`

`self._conversation` grows indefinitely. Each turn appends user message + assistant response + tool results. No sliding window, no truncation, no token counting.

```python
self._conversation: list[dict[str, Any]] = []
# ... appends on every turn, never pruned ...
```

**Impact:**
- Silent API failures or truncation when context exceeds model limits
- Cost waste from sending ever-growing context
- Degraded response quality as old context pollutes newer turns
- Session timeout resets after 8h idle, but active sessions can grow unbounded

**Remediation:** Implement sliding-window context manager. Keep system prompt + memory + last N turns. Summarize or discard older turns when approaching token limit (~120K of 200K).

---

### C-04: Sub-Agent Costs Not Aggregated to Main Budget
**Reporters:** LLM/AI | **Files:** `src/sub_agent.py:72-76`, `src/tools/spawn_team.py:137-149`

Sub-agents track tokens via `on_tokens` callback to JobRegistry, but never report to the main agent's `cost_guard`. The cost guard only sees direct main-agent API calls.

```python
# sub_agent.py — only updates JobRegistry, NOT cost_guard
if self._on_tokens is not None:
    self._on_tokens(step_in, step_out)
```

**Impact:** Daily/monthly cost limits can be exceeded via sub-agent delegation. A $5 daily budget could result in $15+ actual spend.

**Remediation:** Pass cost_guard reference to sub-agents. Call `cost_guard.record_llm_call()` after each sub-agent API call.

---

### C-05: Confirmation Gate Not Enforced in Sub-Agents
**Reporters:** LLM/AI | **Files:** `src/tools/spawn_team.py:142-149`, `src/sub_agent.py`

SubAgentRunner executes tools directly with NO guardrail chain — no confirmation gates, no rate limiter, no scope check.

```python
# SubAgentRunner.run() has NO guardrails at all
runner = SubAgentRunner(config=self._config, tools=subset, ...)
```

Pipeline tool filters out `spawn_team/spawn_agent/run_pipeline` but does NOT filter out `manage_team`, `manage_workspace`, `manage_schedule` — all of which require confirmation in the main agent.

**Impact:** Delegated teams can modify team configuration, workspaces, and schedules without user approval.

**Remediation:** Either pass a minimal guardrail chain to SubAgentRunner, or explicitly filter out all confirmation-requiring tools when building sub-agent tool subsets.

---

### C-06: SQLite Race Conditions in Stores
**Reporters:** Backend, Architect | **Files:** `src/teams/store.py:13`, `src/pipeline/store.py:62`, `src/workspaces/store.py:49`

Three stores use persistent connections with `check_same_thread=False` and no synchronization:

```python
self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
```

Multiple async tasks can execute SQL simultaneously on the same connection. No `asyncio.Lock` protects these stores (unlike JobRegistry which has one).

**Impact:** "Database is locked" errors under concurrent writes. Potential data corruption.

**Remediation:** Add `asyncio.Lock` to each store, or switch to context-manager pattern (like AuditDB/MemoryStore), or use aiosqlite.

---

### C-07: Background Task Exception Swallowing
**Reporters:** Backend | **Files:** `src/tools/spawn_team.py:187-195`, `src/tools/claude_code.py:271-395`

Background tasks created with `asyncio.create_task()` catch exceptions but have failure paths where secondary errors (e.g., `notifier.send()` failure in fallback) are not caught.

**Impact:** Silent failures — team reports lost, Claude Code output never reaches user, no retry or escalation.

**Remediation:** Wrap all fallback paths in try/except. Consider a dead-letter mechanism for failed notifications.

---

### C-08: Missing `await proc.wait()` After Subprocess Kill
**Reporters:** Backend | **File:** `src/tools/claude_code.py:271-283`

After `proc.kill()` on timeout, the code returns without awaiting `proc.wait()`.

**Impact:** Zombie processes accumulate over multiple timeouts.

**Remediation:** Add `await proc.wait()` after `proc.kill()`.

---

### C-09: `asyncio.CancelledError` Suppressed in Pipeline
**Reporters:** Backend | **File:** `src/tools/run_pipeline.py:281-286`

CancelledError is caught, cleanup happens, but the error is not re-raised — it `return`s instead.

```python
except asyncio.CancelledError:
    # ... cleanup ...
    return  # MISSING: raise
```

**Impact:** Violates Python's async cancellation contract. The event loop doesn't know cancellation succeeded.

**Remediation:** Re-raise after cleanup: replace `return` with `raise`.

---

### C-10: Race Condition in Spawn Agent Active Counter
**Reporters:** Backend | **File:** `src/tools/spawn_agent.py:60,77,87`

The `_active` counter check and increment are not atomic across await points:

```python
if self._active >= _MAX_CONCURRENT:  # check
    ...
# ... await point ...
self._active += 1  # increment (separate from check)
```

**Impact:** More than 5 concurrent sub-agents can be spawned, bypassing the limit.

**Remediation:** Use `asyncio.Semaphore(5)` instead of manual counter.

---

### C-11: Workspaces Directory Tracked by Git (Data Leak)
**Reporters:** Manual discovery | **Files:** `.gitignore`, `workspaces/`

The `workspaces/` directory is **not in `.gitignore`**. Two entries are currently tracked:
- `workspaces/.gitkeep` — committed in `c7e402e`
- `workspaces/mongo-test` — tracked as a git submodule (mode `160000`), committed in `339a49a`

Workspaces are user-created external repos managed by the agent. They should never be committed — they may contain private code, credentials, or proprietary data from other projects.

**Impact:** Any `git push` leaks workspace contents (or submodule references) to the remote. CI fails trying to fetch inaccessible submodules (the exit code 128 error in M-24).

**Remediation:**
1. `git rm --cached -r workspaces/mongo-test` and `git rm --cached workspaces/.gitkeep`
2. Remove any `.gitmodules` entry
3. Add `workspaces/` to `.gitignore` (keep only a comment about the directory's purpose)
4. Commit the fix

---

## High-Severity Findings

### H-01: Audit Chain Hash Race Condition
**Reporters:** Backend | **File:** `src/audit/db.py:72-88`

Between reading `prev_hash` and inserting a new record, another async task could insert, causing chain forks.

**Remediation:** Use SERIALIZABLE isolation or a single-threaded writer queue for Tier1 inserts.

---

### H-02: Audit Hook Never Called — Incomplete Audit Trail
**Reporters:** LLM/AI | **Files:** `src/agent.py:244-298`, `src/guardrails/audit_hook.py:27-31`

The audit hook's `check()` always returns `(True, None)`. Its `record()` method is never invoked. Tier2 logs only record tool name, not parameters.

**Impact:** Forensic analysis impossible — can't determine what parameters were passed to allowed tool calls.

**Remediation:** Call `audit_hook.record()` after guardrail chain decision, logging tool name + params + allow/deny.

---

### H-03: Path Traversal in Workspace Management
**Reporters:** Security | **File:** `src/tools/manage_workspace.py:171-172`

`local_path` is user-provided and used directly:

```python
path = Path(local_path).expanduser()
path.mkdir(parents=True, exist_ok=True)
```

**Impact:** Agent can create directories anywhere on the filesystem.

**Remediation:** Validate `local_path` is within `workspaces_base_dir` using `path.resolve().is_relative_to(base_dir)`.

---

### H-04: GitHub Tokens Stored in Plaintext SQLite
**Reporters:** Security | **File:** `src/workspaces/store.py:37,68,76`

`github_token_env` column stores token values in plaintext.

**Remediation:** Store only env var names (references), not actual token values. Resolve at runtime.

---

### H-05: Rate Limiter Scope is Per-Turn Only
**Reporters:** Security, LLM/AI | **File:** `src/guardrails/rate_limiter.py:14-16`

Counter resets on every `reset()` call (each user turn). No per-session or per-day tool call limits.

**Remediation:** Document limitation. Consider adding per-session cumulative limits.

---

### H-06: Weak Type Hints (`object` and `Any`) Throughout Tools
**Reporters:** Architect, Code Quality | **Files:** `src/tools/github_tools.py`, `src/tools/claude_code.py`, `src/sub_agent.py`, and 8+ others

11+ instances of `workspace_store: object`, `notifier: object`, `config: Any` instead of proper Protocol types. This defeats mypy and runtime type checking. `assert isinstance()` used as type guard (stripped in optimized Python).

**Remediation:** Replace `object`/`Any` with proper Protocol types. Use `TYPE_CHECKING` block if circular imports are the concern.

---

### H-07: Module-Level Singleton in `evolve.py`
**Reporters:** Architect | **File:** `src/tools/evolve.py:15`

```python
_SCANNER = CodeScanner()
```

Violates CLAUDE.md rule: "No global singletons except `config`."

**Remediation:** Lazy init inside `execute()` or inject via constructor.

---

### H-08: No Database Backup Strategy
**Reporters:** DevOps | **Files:** `docker-compose.yml`, `data/`

Four SQLite databases with no backup process. Disk failure = total data loss.

**Remediation:** Daily SQLite `.backup` to local disk + cloud sync. Alert on backup failure.

---

### H-09: No Security Scanning in CI
**Reporters:** DevOps | **File:** `.github/workflows/ci.yml`

No `bandit`, `pip-audit`, `semgrep`, or container scanning. Vulnerable dependencies go undetected.

**Remediation:** Add `pip-audit` and `bandit` steps to CI workflow.

---

### H-10: No Dependency Lock File
**Reporters:** DevOps | **File:** `pyproject.toml`

Dependencies use `>=` constraints with no lock file. Builds are non-deterministic.

**Remediation:** Generate and commit `requirements-lock.txt` via `pip-compile`.

---

### H-11: Major Test Coverage Gaps
**Reporters:** QA | **Files:** `tests/`

Modules with **zero tests**:
- `src/tools/calendar_read.py`
- `src/tools/notes.py`
- `src/tools/tasks.py`
- `src/tools/web_search.py`
- `src/guardrails/rate_limiter.py`
- `src/guardrails/confirmation_gate.py`
- `src/guardrails/audit_hook.py`
- `src/sub_agent.py`
- `src/audit/query.py`
- `src/interfaces/cli.py`

**Remediation:** Create test files for each. Phase 1 priority: tasks.py (SQL injection), notes.py (path traversal), rate_limiter, confirmation_gate, sub_agent.

---

### H-12: Circular Dependency Pattern Not Documented/Validated
**Reporters:** Architect | **Files:** `main.py:305-307`, `src/tools/spawn_team.py:73`

Three tools use `set_agent()` post-construction. If `execute()` is called before wiring, features silently degrade (None checks, not errors).

**Remediation:** Document pattern. Add startup validation that all required tools are wired. Consider raising `NotWiredError` instead of silent None fallback.

---

### H-13: Missing structlog Context Binding
**Reporters:** Backend | **Files:** agent.py, tools/, guardrails/

No request/session ID bound to log context. Can't correlate logs across a single user interaction.

**Remediation:** Use `structlog.contextvars.bind_contextvars(session_id=...)` in `_run_turn_locked()`.

---

### H-14: Trust Level Check Silently Passes When Store is None
**Reporters:** Code Quality | **File:** `src/tools/github_tools.py:50-69`

```python
if workspace_store is None:
    return None  # silently passes
```

**Impact:** If workspace_store is misconfigured, trust checks are bypassed entirely.

**Remediation:** Fail explicitly when workspace_store is required but missing.

---

## Medium-Severity Findings

### M-01: Memory Context Injection Size Not Bounded to Model Limits
**File:** `src/agent.py:173-180` — Memory context capped at `max_tokens * 4` chars (heuristic). No actual token counting. Silent truncation.

### M-02: Loop Detector Memory Leak
**File:** `src/guardrails/loop_detector.py:16-24` — Old session data never purged from `_counts` dict.

### M-03: Rate Limiter Off-By-One
**File:** `src/guardrails/rate_limiter.py:21-26` — Counter increments before check, allowing `max + 1` attempts before blocking.

### M-04: Temporary Files in `/tmp` Without Secure Permissions
**File:** `src/interfaces/telegram_bot.py:168,199` — Uses f-string paths in `/tmp/` instead of `tempfile.NamedTemporaryFile()`.

### M-05: Confirmation Gate Timeout Race Condition
**File:** `src/interfaces/telegram_bot.py:274-284` — If user confirms just as timeout fires, confirmation is silently lost.

### M-06: Code Scanner Name Collision Bypass
**File:** `src/guardrails/code_scanner.py` — Proposed tool named `restart.py` could bypass subprocess restriction checks.

### M-07: Tier2 Purge Never Called
**File:** `src/audit/db.py:122-127` — `purge_old_tier2()` exists but is never scheduled or invoked.

### M-08: Cost Rates Duplicated in Two Files
**Files:** `src/agent.py:28-33`, `src/jobs.py:16-21` — Model costs defined independently. Must update both on pricing changes.

### M-09: No Docker HEALTHCHECK
**File:** `Dockerfile` — No health probe for orchestrator liveness detection.

### M-10: Docker Image Bloat from Node/npm
**File:** `Dockerfile:15-27` — Installing NodeJS for claude-code CLI adds ~200MB to image.

### M-11: Blocking File I/O in Async Context (Memory Compactor)
**File:** `src/memory/compactor.py:152-155` — Synchronous file write in async function blocks event loop.

### M-12: Inconsistent Error Message Formats
**Files:** Various tools — Mix of `[ERROR]`, `[BLOCKED]`, plain text errors.

### M-13: Duplicate Notifier Protocol Definitions
**Files:** `src/scheduler.py:18`, `src/tools/send_message.py:7`, `src/tools/spawn_team.py:27`, `src/tools/run_pipeline.py:73` — Same Protocol defined 4 times.

### M-14: Duplicate Workspace Resolution Logic
**Files:** `src/tools/github_tools.py:33-47`, `src/tools/claude_code.py:174-184` — Same pattern repeated.

### M-15: `_resolve_cwd` Returns Mixed Union Type
**File:** `src/tools/github_tools.py:33` — Returns `str | tuple[None, str]`. Callers must `isinstance` check.

### M-16: Over-Mocking in Tests Hides Integration Bugs
**File:** `tests/unit/agent/test_conversation_safety.py:82-100` — Mocks assume single content block. Real API returns multiple block types.

### M-17: Scheduler Exception Suppression Too Broad
**File:** `src/scheduler.py:60,80,92,104` — `contextlib.suppress(Exception)` hides all errors silently.

### M-18: LaunchAgent Requires Manual Path Substitution
**File:** `com.personalassistant.plist:10-17` — Placeholder path must be hand-edited.

### M-19: LaunchAgent Log Files Unbounded
**File:** `com.personalassistant.plist:30-34` — No log rotation. Crash loops can fill disk.

### M-20: No File-Based Logging / Remote Aggregation
**File:** `main.py:28-38` — Logs go to stdout only. Lost on crash.

### M-21: CI Coverage Not Reported to External Service
**File:** `.github/workflows/ci.yml:17` — Coverage enforced at 75% but not tracked over time.

### M-22: Sub-Agent Max Steps Reached Without Clear User Signal
**File:** `src/sub_agent.py:117-118` — Returns generic message; main agent may present partial results as complete.

### M-23: CI Actions Using Deprecated Node.js 20
**File:** `.github/workflows/ci.yml:9-10` — `actions/checkout@v4` and `actions/setup-python@v5` run on Node.js 20, which is deprecated. GitHub will force Node.js 24 starting June 2, 2026. CI will emit warnings now and may break after that date.

**Remediation:** Update to versions that support Node.js 24 when available, or set `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` in the workflow to opt in early.

### M-24: Git Checkout Fails with Exit Code 128 in CI
**File:** `.github/workflows/ci.yml:9` — `actions/checkout@v4` fails with `/usr/bin/git` exit code 128. This typically indicates authentication issues (private repo without proper token), shallow clone problems, or submodule fetch failures (the repo has a `workspaces/mongo-test` submodule that may not be accessible in CI).

**Remediation:** Investigate root cause:
- If submodule related: add `submodules: false` to checkout step or ensure submodule URLs are accessible
- If auth related: verify `GITHUB_TOKEN` permissions in workflow
- If shallow clone: add `fetch-depth: 0` to checkout step

---

## Low-Severity Findings

### L-01: `datetime.utcnow()` Deprecated in Python 3.12
**File:** `src/audit/events.py:40` — Should use `datetime.now(UTC)`. Will be removed in 3.13.

### L-02: Hardcoded Timeout/Limit Constants
**Files:** `claude_code.py:34-35`, `restart.py:20`, `sub_agent.py:13`, `spawn_agent.py:12` — Should be configurable.

### L-03: Magic Token Numbers in Multiple Places
**Files:** `sub_agent.py:24`, `spawn_agent.py:58` — `max_tokens` defaults (4096, 8192) not centralized.

### L-04: Global `_spinner_active` State in CLI
**File:** `src/interfaces/cli.py:17,22-23` — Mutable module-level global read from main.py.

### L-05: Missing Docstrings on Public Methods
**Files:** Multiple — `JobRegistry.start()`, `MemoryStore.append_turn()`, tool `execute()` methods.

### L-06: No Upper Bounds on Dependencies
**File:** `pyproject.toml` — `anthropic>=0.40.0` with no cap. Major version bump could break.

### L-07: Docker Entrypoint Signal Handling
**File:** `docker-compose.yml:50` — Shell entrypoint doesn't `exec`, so SIGTERM may not reach Python.

### L-08: No Database Integrity Checks at Startup
**Files:** Various stores — No `PRAGMA integrity_check` on boot.

### L-09: Inconsistent Enum Patterns
**Files:** `src/jobs.py`, `src/pipeline/store.py`, `src/workspaces/store.py` — Mix of StrEnum and class-with-str-attrs.

### L-10: Test Organization Could Be Improved
**Files:** `tests/unit/test_jobs.py`, `tests/unit/test_scheduler.py` — Should be in subdirectories matching src/.

### L-11: No Property-Based or Snapshot Tests
**Files:** `tests/` — Pure functions ideal for hypothesis but none exist.

### L-12: `.env.example` Missing Optional Field Documentation
**File:** `.env.example` — Doesn't indicate which keys are required vs. optional.

### L-13: Session Idle Timer Uses Monotonic Clock (Naming Confusion)
**File:** `src/agent.py:96,148` — `_last_activity` uses `time.monotonic()` which is correct but naming suggests wall-clock.

### L-14: Successful Tool Results Not Logged in Audit
**File:** `src/agent.py:291-293` — Tier2 logs tool name but not result content.

### L-15: Docker DNS Hardcoded
**File:** `docker-compose.yml:20-22` — Google/Cloudflare DNS only, no fallback.

---

## Positive Observations

The review team identified several areas of strong practice:

| Area | Assessment |
|------|------------|
| **Protocol-based interfaces** | Excellent. No ABCs, clean structural subtyping throughout |
| **Layering** | Clean. Tools → Stores → External. No improper upward dependencies |
| **Guardrail chain** | Deterministic, fail-fast, well-ordered. Strong design |
| **Audit chain hash** | Cryptographically sound Tier1 chain. Immutable security events |
| **Dependency injection** | Consistent at startup via `main.py`. Clear wiring |
| **Async patterns** | Proper `asyncio.Lock`, `create_task`, `wait_for` usage |
| **structlog usage** | Consistent structured logging (except missing context binding) |
| **Non-root Docker** | Container runs as `enki:1000`. Good security practice |
| **Read-only mounts** | Guardrails and core agent mounted read-only in Docker |
| **Code scanner** | AST-based validation for proposed tools. Blocks dangerous imports |
| **Background task pattern** | Fire-and-forget with Enki relay is well-architected |
| **Memory compaction** | Post-session haiku distillation into durable facts is innovative |
| **Type hints** | Near-complete coverage (with exceptions noted above) |
| **`from __future__ import annotations`** | Consistent PEP 563 compliance across all 50+ files |

---

## Prioritized Action Plan

### Phase 0 — Immediate (Today)
| # | Action | Finding |
|---|--------|---------|
| 1 | Rotate ALL credentials (Anthropic, Telegram, GitHub, Brave) | C-01 |
| 2 | `git rm --cached .env` and scrub from history | C-01 |

### Phase 1 — Critical Fixes (Week 1)
| # | Action | Finding |
|---|--------|---------|
| 3 | Fix SQL injection in `tasks.py` — parameterized queries | C-02 |
| 4 | Add `asyncio.Lock` to TeamsStore, PipelineStore, WorkspaceStore | C-06 |
| 5 | Implement conversation sliding window | C-03 |
| 6 | Aggregate sub-agent costs to main cost_guard | C-04 |
| 7 | Filter confirmation-requiring tools from sub-agent tool subsets | C-05 |
| 8 | Add `await proc.wait()` after kill in claude_code.py | C-08 |
| 9 | Re-raise CancelledError in run_pipeline.py | C-09 |
| 10 | Replace active counter with `asyncio.Semaphore(5)` | C-10 |

### Phase 2 — High-Priority (Week 2-3)
| # | Action | Finding |
|---|--------|---------|
| 11 | Validate path traversal in manage_workspace | H-03 |
| 12 | Wire audit_hook.record() after guardrail decisions | H-02 |
| 13 | Fix weak type hints → proper Protocols | H-06 |
| 14 | Add startup validation for set_agent() wiring | H-12 |
| 15 | Create test files for 10 untested modules | H-11 |
| 16 | Add `pip-audit` + `bandit` to CI | H-09 |
| 17 | Generate and commit dependency lock file | H-10 |
| 18 | Implement database backup strategy | H-08 |

### Phase 3 — Hardening (Week 3-4)
| # | Action | Finding |
|---|--------|---------|
| 19 | Schedule Tier2 purge job | M-07 |
| 20 | Consolidate Notifier protocol to single definition | M-13 |
| 21 | Add structlog context binding (session_id) | H-13 |
| 22 | Fix rate limiter off-by-one | M-03 |
| 23 | Use tempfile module for Telegram downloads | M-04 |
| 24 | Centralize model cost constants | M-08 |
| 25 | Add Docker HEALTHCHECK | M-09 |
| 26 | Fix entrypoint signal handling | L-07 |

### Phase 4 — Polish (Ongoing)
| # | Action | Finding |
|---|--------|---------|
| 27 | Replace `datetime.utcnow()` | L-01 |
| 28 | Make timeout/limit constants configurable | L-02 |
| 29 | Standardize error message format | M-12 |
| 30 | Add property-based tests | L-11 |
| 31 | Improve test organization | L-10 |
| 32 | Add deployment documentation | Various |

---

## Appendix: Full Finding Index

| ID | Title | Severity | Reporters |
|----|-------|----------|-----------|
| C-01 | Exposed credentials in `.env` | CRITICAL | Security, DevOps |
| C-02 | SQL injection in `tasks.py` | CRITICAL | Security, Code Quality, Backend |
| C-03 | Unbounded conversation context | CRITICAL | LLM/AI, Backend |
| C-04 | Sub-agent costs not aggregated | CRITICAL | LLM/AI |
| C-05 | Confirmation gate bypassed in sub-agents | CRITICAL | LLM/AI |
| C-06 | SQLite race conditions in stores | CRITICAL | Backend, Architect |
| C-07 | Background task exception swallowing | CRITICAL | Backend |
| C-08 | Missing `await proc.wait()` after kill | CRITICAL | Backend |
| C-09 | CancelledError suppressed in pipeline | CRITICAL | Backend |
| C-10 | Race condition in spawn agent counter | CRITICAL | Backend |
| C-11 | Workspaces directory tracked by git (data leak) | CRITICAL | Manual |
| H-01 | Audit chain hash race condition | HIGH | Backend |
| H-02 | Audit hook never called | HIGH | LLM/AI |
| H-03 | Path traversal in workspace management | HIGH | Security |
| H-04 | GitHub tokens in plaintext SQLite | HIGH | Security |
| H-05 | Rate limiter scope per-turn only | HIGH | Security, LLM/AI |
| H-06 | Weak type hints (`object`/`Any`) | HIGH | Architect, Code Quality |
| H-07 | Module-level singleton in evolve.py | HIGH | Architect |
| H-08 | No database backup strategy | HIGH | DevOps |
| H-09 | No security scanning in CI | HIGH | DevOps |
| H-10 | No dependency lock file | HIGH | DevOps |
| H-11 | Major test coverage gaps (10 modules) | HIGH | QA |
| H-12 | Circular dep pattern undocumented | HIGH | Architect |
| H-13 | Missing structlog context binding | HIGH | Backend |
| H-14 | Trust check silently passes on None store | HIGH | Code Quality |
| M-01 | Memory context size not bounded to model | MEDIUM | LLM/AI |
| M-02 | Loop detector memory leak | MEDIUM | LLM/AI |
| M-03 | Rate limiter off-by-one | MEDIUM | LLM/AI |
| M-04 | Insecure temp file creation | MEDIUM | Security |
| M-05 | Confirmation timeout race condition | MEDIUM | Security |
| M-06 | Code scanner name collision bypass | MEDIUM | Security |
| M-07 | Tier2 purge never called | MEDIUM | LLM/AI, DevOps |
| M-08 | Cost rates duplicated | MEDIUM | LLM/AI |
| M-09 | No Docker HEALTHCHECK | MEDIUM | DevOps |
| M-10 | Docker image bloat | MEDIUM | DevOps |
| M-11 | Blocking I/O in async (compactor) | MEDIUM | Backend |
| M-12 | Inconsistent error message formats | MEDIUM | Code Quality |
| M-13 | Duplicate Notifier protocol definitions | MEDIUM | Code Quality |
| M-14 | Duplicate workspace resolution logic | MEDIUM | Code Quality |
| M-15 | Mixed union return type in `_resolve_cwd` | MEDIUM | Code Quality |
| M-16 | Over-mocking hides integration bugs | MEDIUM | QA |
| M-17 | Scheduler exception suppression too broad | MEDIUM | DevOps |
| M-18 | LaunchAgent manual path substitution | MEDIUM | DevOps |
| M-19 | LaunchAgent log files unbounded | MEDIUM | DevOps |
| M-20 | No file-based logging | MEDIUM | DevOps |
| M-21 | CI coverage not reported externally | MEDIUM | DevOps |
| M-22 | Sub-agent max steps silent failure | MEDIUM | LLM/AI |
| M-23 | CI actions using deprecated Node.js 20 | MEDIUM | CI warnings |
| M-24 | Git checkout fails (exit code 128) in CI | MEDIUM | CI warnings |
| L-01 | `datetime.utcnow()` deprecated | LOW | Code Quality |
| L-02 | Hardcoded timeout constants | LOW | Code Quality |
| L-03 | Magic token numbers | LOW | Code Quality |
| L-04 | Global `_spinner_active` state | LOW | Architect, Code Quality |
| L-05 | Missing docstrings | LOW | Code Quality |
| L-06 | No upper bounds on deps | LOW | DevOps |
| L-07 | Entrypoint signal handling | LOW | DevOps |
| L-08 | No DB integrity checks at startup | LOW | DevOps |
| L-09 | Inconsistent enum patterns | LOW | Code Quality |
| L-10 | Test organization | LOW | QA |
| L-11 | No property-based tests | LOW | QA |
| L-12 | `.env.example` incomplete | LOW | DevOps |
| L-13 | Session timer naming confusion | LOW | LLM/AI |
| L-14 | Tool results not logged in audit | LOW | LLM/AI |
| L-15 | Docker DNS hardcoded | LOW | DevOps |

---

*Report generated 2026-03-11 by 7-specialist review team. No code changes included.*
