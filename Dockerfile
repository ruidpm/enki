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
        curl git ffmpeg sqlite3 \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && pip install --no-cache-dir gcalcli \
    && rm -rf /var/lib/apt/lists/*

# Compiled Python deps (sqlite-vec .so, anthropic, structlog, etc.)
COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages

WORKDIR /app

# Non-root user — required by claude --dangerously-skip-permissions (refuses to run as root)
RUN useradd -m -u 1000 enki \
    && chown -R enki:enki /app

# src/ importable via PYTHONPATH — volume mounts work naturally
ENV PYTHONPATH=/app

COPY src/ ./src/
COPY main.py soul.md entrypoint.sh ./
RUN chmod +x entrypoint.sh && chown -R enki:enki /app

USER enki

CMD ["./entrypoint.sh"]
