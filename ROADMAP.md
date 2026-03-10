# Enki — Roadmap

## Status: voice/photo + prompt caching + memory cleanup live (2026-03-10)

---

## Done

| What | Notes |
|---|---|
| Project skeleton (pyproject.toml, Dockerfile, docker-compose) | pip-based, no uv in Docker |
| Config (pydantic-settings) | all fields typed, fails fast |
| Guardrail chain | allowlist → scope_check → loop_detector → rate_limiter → cost_guard → confirmation_gate → audit_hook |
| Audit trail | Tier1 (chain-hashed, forever) / Tier2 (30-day metadata) / Tier3 (opt-in debug) |
| Memory store | SQLite + FTS5 + daily markdown logs + facts.md; compactor wired in CLI + Telegram |
| Agent loop | model routing (haiku/sonnet/opus), tool dispatch, agentic loop |
| Tools registered | tasks, web_search, notes, calendar_read, email_read (optional), git_status/diff/commit/push_branch/create_pr, propose_tool, request_restart, run_claude_code, spawn_agent |
| soul.md | injected as system prompt |
| CLI interface | `python main.py chat` — interactive REPL |
| Telegram bot | polling, inline keyboard confirmations, compactor at shutdown |
| Memory compactor | wired in CLI + Telegram, distills sessions → facts.md |
| Scheduler | APScheduler cron jobs; morning_briefing + deadline_check + eod_team_report by default |
| Claude Code tool | spawns `claude --dangerously-skip-permissions`, double-confirm, 5-min cooldown |
| Sub-agent spawning | SubAgentRunner, SpawnAgentTool — isolated loop, max 5 concurrent, depth limit 1 |
| Security test suite | 66 tests: code scanner bypasses, scope check, prompt injection — all green |
| Production deployment | Dockerfile, docker-compose, LaunchAgent scripts |
| **COO Orchestrator — persistent teams** | TeamsStore (SQLite), SpawnTeamTool (fire-and-forget, reports via Enki), TeamReportTool, ManageTeamTool — confirmation-gated create/update/deactivate |
| **Dynamic scheduler** | ScheduleStore (SQLite), ListScheduleTool + ManageScheduleTool (add/pause/resume/remove), cron validation, survive restarts, manage_schedule in REQUIRES_CONFIRM |
| **Token tracking** | SubAgentRunner.run() returns (text, tokens); team monthly budget enforcement is no longer blind |
| **Test suite** | 331 tests passing |
| **Telegram UX** | typing indicator, turn lock, /newsession, proactive ack (send_message), session auto-reset |
| **Token + cost tracking** | JobRegistry tracks tokens/cost per job; job_status shows live spend |
| **Prompt caching** | cache_control ephemeral on system prompt + last tool def; works in SDK 0.84+ natively |
| **Telegram voice** | local openai-whisper (tiny model, free, lazy-loaded, bundleable in Docker) |
| **Telegram photo** | base64 vision content blocks → Claude; caption as text prompt |
| **Memory cleanup** | MemoryCompactor.clean_facts() — weekly haiku prune of facts.md, auto-triggered at startup |
| **manage_schedule update** | Enki can edit existing job prompts/cron; graceful degradation without live scheduler |
| **Guardrail limits raised** | 5M tokens/session, $50/day, $300/month, 1000 LLM calls |

---

## Roadmap (priority order)

### ~~1. Job status visibility~~ ✓ DONE
- JobRegistry (in-memory), JobStatusTool, wired into RunClaudeCodeTool + RunPipelineTool

### ~~1. Telegram end-to-end verification~~ ✓ DONE
- Voice, photo, text, inline confirmations, /newsession, /cost — all verified on mobile

### 1. remove_tool capability  ← NEXT
- Agent can't remove a tool it proposed — needs `remove_tool(name)` that unregisters + deletes file
- Needs user confirmation (in REQUIRES_CONFIRM)
- Must refuse IMMUTABLE_CORE tools

### 3. Memory: drop FTS5, keep embeddings infrastructure
- FTS5 keyword search is dead code — never called in the active path, inferior to embeddings anyway
- Drop: `turns_fts` virtual table + triggers, `search_fts()`, `_sanitize_fts_query()`, legacy else branch in `build_context()`
- Keep: `turns` table (compactor reads it), `embeddings` table + `sqlite-vec` dep (needed for future vector recall)
- Migration note: must drop FTS triggers BEFORE dropping turns_fts or inserts will crash on existing DBs

### 4. Tool context window strategy (deferred — not worth it yet)
- With prompt caching active, 27 tools costs ~$0.0004/turn after first call — negligible
- Keyword routing breaks cache stability and can cost MORE than sending everything
- Revisit if/when tool count exceeds ~60 and model quality degrades; use semantic embeddings then, not keywords

### 5. Known security gaps (document/fix)
- `ftp://` and `//` protocol-relative URLs not blocked by scope_check (documented in test, not fixed)
- URL-encoded path traversal (`..%2F`) not caught (documented in test, not fixed)
- Fix: harden `scope_check.py` to reject non-http/https schemes and URL-decode before traversal check

### 6. Semantic memory recall (vector search over past conversations)
- Enables: "remember when we discussed X last month" style recall across sessions
- Approach: Voyage AI embeddings (Anthropic-recommended, $0.02/MTok) + sqlite-vec similarity search
- On `append_turn()`: generate embedding in background, store in `embeddings` table
- On `build_context()`: embed query → top-K similar past turns injected alongside facts.md
- Depends on item 3 (FTS cleanup) being done first
- Effort: ~1 day

### ~~7. Memory cleanup~~ ✓ DONE
### ~~8. Telegram audio + image support~~ ✓ DONE
### ~~9. Prompt caching~~ ✓ DONE

---

## Known bugs (open)
| Bug | Notes |
|---|---|
| CLI spinner display artifacts | `⠙ thinking...` bleeds into structlog output lines mid-spin. Fix: `_spinner_active` flag + `_SpinnerClearProcessor` in main.py — included in build routing fix PR |
| `git_diff`/`git_status`/CCC run in wrong workspace | All git tools and `run_claude_code` default to process CWD (personal-assistant repo) when no `workspace_id` is given. Pipeline stages build into `~/projects/snake-game` but Enki diffs its own repo. **Structural fix needed**: git tools must require `workspace_id` and resolve CWD from `WorkspaceStore`; refuse to operate without it. `run_claude_code` must also `cd` into the workspace path, not process CWD. |

## Known bugs fixed
| Bug | Fix |
|---|---|
| Telegram bot deadlock on concurrent messages | Added `_turn_lock` — incoming messages while a turn is in progress get bounced with "Still processing" reply |
| `SpawnTeamTool` background tasks not cancellable | Wired `job_registry` into `SpawnTeamTool`; tasks stored via `set_task()` and cancellable via `job_registry.cancel()` |
| `manage_pipeline(abort)` didn't kill background task | `RunPipelineTool` stores asyncio Task in JobRegistry after `create_task`; `CancelledError` caught in `_run_background` |
| Pipeline `ask_double_confirm` said "Restart requested" | Fixed copy-paste — now uses `reason` as title |

## Known bugs fixed
| Bug | Fix |
|---|---|
| `register()` blocked initial registration of IMMUTABLE_CORE tools | Only block overwrite if already in registry |
| FTS5 syntax error on raw user input with `?`, `:`, `AND/OR/NOT` | `_sanitize_fts_query()` strips special chars and operators |
| Dockerfile used uv — broke inside container | Switched to `pip install -e "."` directly |
| `tokens_used=0` hardcoded in spawn_team — budget enforcement blind | SubAgentRunner.run() now returns (text, tokens); actual count logged |
