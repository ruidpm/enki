# Enki

A self-evolving AI personal assistant and software engineering orchestrator. Runs on Claude, lives in your terminal and Telegram, owns its own code.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What it is

Enki is a personal AI assistant built for people who want full ownership of their AI stack. No cloud dependencies beyond the APIs you choose, no opaque runtimes, no vendor lock-in. Every line of code is yours, every decision is audited, every guardrail is deterministic.

It does two things well:

**Personal assistant** — answers questions, manages tasks, searches the web, reads your calendar and email, sends proactive alerts on a schedule you control.

**Software engineering COO** — manages external codebases autonomously. You give it a task; it runs the full pipeline: research → scope → plan → implement (via Claude Code) → test → review → PR. You review and merge. It can work on multiple workspaces in parallel while you sleep.

---

## Why this exists

OpenClaw (a popular AI assistant framework) had 5 CVEs published in a single week including an RCE via WebSocket, two command injection vulnerabilities, and ~20% malicious skill packages on their marketplace. 42,000+ exposed instances were identified.

Enki is a clean-room alternative built on the opposite philosophy: every guardrail is code, not a prompt. The agent cannot bypass its own constraints by being persuaded.

---

## Features

### Interfaces
- **Telegram** (primary) — inline confirmation prompts, proactive notifications, slash commands
- **CLI** — interactive REPL with thinking spinner and background task notifications

### Memory
- Persistent across sessions — facts distilled by Haiku after each conversation
- Daily conversation logs in Markdown (`memory/logs/YYYY-MM-DD.md`)
- `memory/facts.md` — structured knowledge that grows over time

### Guardrails (deterministic, not prompt-based)
```
tool_call
  → allowlist_check      # registered tools only
  → scope_check          # URL allowlist, path traversal detection
  → loop_detector        # same tool+params 3× → BLOCK
  → rate_limiter         # max 10 tool calls per turn
  → cost_guard           # token/$ budget enforcement
  → confirmation_gate    # write ops require Y/N
  → audit_hook           # every event logged
  → EXECUTE
```

### Audit trail (3 tiers)
- **Tier 1** — security events, retained forever, SHA-256 chain-hashed (tamper-evident)
- **Tier 2** — activity metadata (no content), 30-day rolling
- **Tier 3** — full debug content, opt-in, 7-day auto-purge

### Self-evolution
- `propose_tool` — agent proposes new Python tools
- Static code scanner blocks `subprocess`, `eval`, `exec`, unsafe imports
- User reviews full diff + test results before activation
- Immutable core files enforced by Docker volume mounts (read-only)
- Claude Code hooks (`scripts/cc_guard.py`) provide a second enforcement layer

### Engineering orchestration
- **Persistent teams** — researcher, architect, backend-dev, fe-dev, qa, devops
- **Workspace registry** — register any local Git repo with trust level and language
- **`run_pipeline`** — autonomous RESEARCH→SCOPE→PLAN→IMPLEMENT→TEST→REVIEW→PR pipeline as a background job
- **`run_claude_code`** — spawn Claude Code in any registered workspace
- **Workspace-aware git/GitHub tools** — `git_commit`, `git_push_branch`, `create_pr` all accept `workspace_id`

### Cost controls
- Per-session token budget (default: 100k tokens)
- Per-day and per-month USD limits
- Autonomous turn limit (max 5 consecutive agent-initiated turns)
- Per-team monthly token budgets
- Real-time spend tracking via `/cost` Telegram command

### Scheduler
- Persistent cron jobs (SQLite-backed, survive restarts)
- Default jobs: morning briefing, deadline check, EOD team report
- Add/pause/resume/remove jobs via natural language

---

## Architecture

```
enki/
├── main.py                     # Entry point — CLI + Telegram commands
├── soul.md                     # Enki's personality and instructions
│
├── src/
│   ├── agent.py                # Claude API agentic loop, model routing
│   ├── sub_agent.py            # Isolated sub-agent runner (restricted toolset)
│   ├── config.py               # Settings (pydantic-settings, reads .env)
│   ├── scheduler.py            # APScheduler cron jobs
│   │
│   ├── guardrails/             # Deterministic hook chain
│   ├── audit/                  # Tamper-evident audit DB (3 tiers)
│   ├── memory/                 # Store + compactor (SQLite + Markdown)
│   ├── interfaces/             # Telegram bot + CLI REPL
│   │
│   ├── tools/                  # All registered tools
│   ├── teams/                  # Persistent team registry + templates
│   ├── workspaces/             # External workspace registry
│   ├── pipeline/               # Pipeline store (stage tracking + artifacts)
│   └── schedule/               # Persistent schedule store
│
├── scripts/
│   └── cc_guard.py             # Claude Code PreToolUse hook (hard path enforcement)
│
├── tests/
│   ├── unit/                   # Per-module unit tests
│   ├── integration/            # Full turn tests
│   └── security/               # Prompt injection, scanner bypass, scope check bypass
│
└── data/                       # SQLite databases (gitignored)
```

**Model routing:**
| Task | Model |
|---|---|
| Simple lookup, formatting | `claude-haiku-4-5` |
| Research, multi-step, tool use | `claude-sonnet-4-6` (default) |
| Complex planning, code gen | `claude-opus-4-6` |

---

## Quick start

### Prerequisites
- Python 3.12+
- `claude` CLI ([Claude Code](https://claude.ai/code)) — for `run_claude_code` and `run_pipeline`
- `gh` CLI ([GitHub CLI](https://cli.github.com/)) — for PR creation
- Docker + Docker Compose (for production deployment)

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/enki.git
cd enki

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env`. Required keys:

```env
ANTHROPIC_API_KEY=sk-ant-...

# Telegram (required for bot mode)
TELEGRAM_BOT_TOKEN=...     # from @BotFather
TELEGRAM_CHAT_ID=...       # your personal chat ID

# Web search (free tier: 2k/month at api.search.brave.com)
BRAVE_SEARCH_API_KEY=...
```

See [.env.example](.env.example) for all options including cost limits and model routing.

### 3. Run

```bash
# Interactive CLI
python main.py chat

# Telegram bot (background, with scheduler)
python main.py telegram
```

---

## Docker (recommended for always-on)

```bash
docker-compose up --build -d
docker-compose logs -f enki
```

The container runs as a non-root user, with immutable core files mounted read-only. Telegram startup/shutdown alerts fire automatically via [entrypoint.sh](entrypoint.sh).

Volume layout:
- `./data` — SQLite databases (tasks, audit, memory, teams)
- `./memory` — Markdown conversation logs and facts
- `./workspaces` — registered workspace directories
- `./src/tools` — tools directory (agent can add tools here)
- `./src/guardrails`, `./src/audit`, `./src/agent.py`, `./main.py` — **read-only**

---

## Google Calendar setup

Calendar integration uses [gcalcli](https://github.com/insanum/gcalcli). It requires a one-time OAuth setup on your Mac, then the container picks up the credentials automatically.

### Step 1 — Create a Google Cloud OAuth app

gcalcli needs its own OAuth client ID (Google doesn't allow sharing one across users).

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a new project
2. **APIs & Services → Enable APIs** → search for "Google Calendar API" → Enable
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Download the JSON file → save it somewhere, e.g. `~/client_secret.json`
4. **APIs & Services → OAuth consent screen** → set to "External", complete the form, then under **Test users → Add users** add your own Gmail address

### Step 2 — Authenticate on your Mac

Open the downloaded JSON. Under `"installed"`, copy the `client_id` and `client_secret` values.

```bash
pip install gcalcli
gcalcli --client-id "YOUR_CLIENT_ID" --client-secret "YOUR_CLIENT_SECRET" agenda
# Browser opens → sign in with Google → grant access
# Token is saved to ~/.config/gcalcli/oauth
```

No redirect URLs needed — Desktop app type handles that automatically.

Verify it works:
```bash
gcalcli agenda   # should show your upcoming events
```

### Step 3 — Restart the container

`docker-compose.yml` already mounts `~/.config/gcalcli` read-only into the container. Just restart:

```bash
docker-compose restart enki
```

Enki can now read your calendar. The `client_secret.json` file is only needed for the initial auth — keep it somewhere safe but you don't need to reference it again.

---

## Configuration reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | — | Anthropic API key |
| `TELEGRAM_BOT_TOKEN` | for bot | — | From @BotFather |
| `TELEGRAM_CHAT_ID` | for bot | — | Your Telegram user ID |
| `BRAVE_SEARCH_API_KEY` | for search | — | Brave Search API key |
| `MAX_TOKENS_PER_SESSION` | no | `100000` | Session token cap |
| `MAX_DAILY_COST_USD` | no | `5.0` | Daily spend limit |
| `MAX_MONTHLY_COST_USD` | no | `30.0` | Monthly spend limit |
| `MAX_AUTONOMOUS_TURNS` | no | `5` | Max agent-initiated turns |
| `IMAP_HOST` | no | — | Email server (enables email tool) |
| `IMAP_USER` | no | — | Email address |
| `IMAP_PASSWORD` | no | — | App password |
| `DEBUG_AUDIT` | no | `false` | Enable Tier 3 full debug audit |

---

## Usage

### CLI
```
You> What tasks do I have this week?
You> Search for the latest news on AI agents
You> Build a REST API for user authentication in my myapp workspace
You> Show team report
You> List my scheduled jobs
```

### Registering an external workspace
```
You> Add workspace id=myapp, name="My App", path=/path/to/repo, language=typescript
```

### Running the full engineering pipeline
```
You> Build a leaderboard feature for workspace myapp
```
Enki confirms once, then runs RESEARCH→SCOPE→PLAN→IMPLEMENT→TEST→REVIEW→PR autonomously as a background job. You get a Telegram notification after each stage and a PR link when done.

### Telegram slash commands
| Command | Description |
|---|---|
| `/start` | Check Enki is alive |
| `/newsession` | Clear conversation history, start fresh |
| `/cost` | Token usage and spend today/month |
| `/audit` | Last 5 security events |

### Proposing a new tool
```
You> I need a tool to fetch cryptocurrency prices. Propose it.
```
Enki writes the tool, runs the static scanner, sends you a diff to approve. One `y` activates it live — no restart needed.

---

## Development

```bash
# Run tests
.venv/bin/pytest

# With coverage
.venv/bin/pytest --cov --cov-report=term-missing

# Type check
.venv/bin/mypy --strict src/

# Lint
.venv/bin/ruff check .
```

---

## Security

Key properties:
- All guardrails are deterministic code — no prompt-based rules
- Immutable core: `guardrails/`, `audit/`, `agent.py`, `main.py` are read-only in Docker
- No shell execution except two hardcoded calls: `docker-compose restart` (restart tool) and `claude --dangerously-skip-permissions` (CCC tool)
- Static code scanner on all proposed tool code — blocks unsafe imports before they reach disk
- Claude Code PreToolUse hooks enforce protected paths at the CLI level
- Three-tier audit trail, Tier 1 is SHA-256 chain-hashed and retained forever

---

## License

[MIT](LICENSE)
