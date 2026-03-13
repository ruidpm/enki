# ── Stage 1: build native Python extensions ──────────────────────────────────
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# ── Stage 2: runtime (no gcc, no pip install) ─────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ffmpeg sqlite3 procps \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && pip install --no-cache-dir gcalcli \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Compiled Python deps (sqlite-vec .so, anthropic, structlog, etc.)
COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages

WORKDIR /app

# Non-root user — required by claude --dangerously-skip-permissions (refuses to run as root)
RUN useradd -m -u 1000 enki \
    && chown -R enki:enki /app

# Playwright + Chromium — install as enki so browsers land in /home/enki/.cache
USER enki
RUN python -m playwright install --with-deps chromium
USER root

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
