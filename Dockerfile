# ── Stage 1: install Python dependencies (cached until pyproject.toml changes) ─
FROM python:3.12-slim AS deps

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Copy ONLY dependency metadata — source changes won't invalidate this layer
COPY pyproject.toml README.md LICENSE ./
# Stub src/ so hatchling can resolve the package without real source code
RUN mkdir -p src && touch src/__init__.py \
    && pip install --no-cache-dir .

# ── Stage 2: install app code on top of cached deps ──────────────────────────
FROM deps AS builder

COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

# ── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim

# Layer 1: stable system packages (rarely changes)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ffmpeg sqlite3 procps \
    && rm -rf /var/lib/apt/lists/*

# Layer 2: GitHub CLI (rarely changes)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Layer 3: Node.js + Claude Code (changes when claude-code updates)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

# Layer 4: gcalcli (rarely changes)
RUN pip install --no-cache-dir gcalcli

# Layer 5: Playwright + Chromium — only rebuilds when base image or version changes
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN pip install --no-cache-dir "playwright>=1.40" \
    && python -m playwright install --with-deps chromium

# Compiled Python deps (anthropic, structlog, torch, etc.)
# This overwrites the standalone playwright pip package above with the builder's
# version (same range), but browsers at /opt/playwright-browsers are untouched.
COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages

WORKDIR /app

# Non-root user — required by claude --dangerously-skip-permissions (refuses to run as root)
RUN useradd -m -u 1000 enki \
    && mkdir -p /home/enki/.cache/huggingface \
    && chown -R enki:enki /home/enki/.cache /app

# src/ importable via PYTHONPATH — volume mounts work naturally
ENV PYTHONPATH=/app

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY .claude/ ./.claude/
COPY main.py soul.md entrypoint.sh ./
RUN chmod +x entrypoint.sh && chown -R enki:enki /app

USER enki
RUN git config --global --add safe.directory '*'

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep -f "python main.py" > /dev/null || exit 1

CMD ["./entrypoint.sh"]
