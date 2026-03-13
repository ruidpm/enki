"""Application configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.models import ModelId


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API keys
    anthropic_api_key: str
    telegram_bot_token: str
    brave_search_api_key: str
    telegram_chat_id: str

    # Workspace base directory — code dirs for agent-managed projects
    workspaces_base_dir: Path = Path("workspaces")

    # Database paths
    audit_db_path: Path = Path("data/audit.db")
    memory_db_path: Path = Path("data/memory.db")
    tasks_db_path: Path = Path("data/tasks.db")
    audit_debug_db_path: Path = Path("data/audit_debug.db")

    # Cost controls
    max_tokens_per_session: int = Field(default=5_000_000, ge=1_000)
    max_daily_cost_usd: float = Field(default=50.0, gt=0)
    max_monthly_cost_usd: float = Field(default=300.0, gt=0)
    max_tool_calls_per_turn: int = Field(default=20, ge=1)
    max_llm_calls_per_session: int = Field(default=1000, ge=1)
    max_autonomous_turns: int = Field(default=10, ge=1)
    session_timeout_hours: float = Field(default=8.0, gt=0)
    loop_detection_threshold: int = Field(default=3, ge=2)
    max_context_tokens: int = Field(default=120_000, ge=1000)

    # Model routing
    default_model: str = ModelId.SONNET
    haiku_model: str = ModelId.HAIKU
    opus_model: str = ModelId.OPUS

    # Email (optional — tool skipped if not set)
    imap_host: str | None = None
    imap_user: str | None = None
    imap_password: str | None = None

    # Timeouts & limits (configurable — were previously hardcoded)
    restart_cooldown_seconds: int = Field(default=600, ge=0)
    confirm_timeout_seconds: int = Field(default=300, ge=1)
    sub_agent_max_steps: int = Field(default=80, ge=1)
    claude_code_timeout_seconds: int = Field(default=600, ge=10)
    claude_code_cooldown_seconds: int = Field(default=300, ge=0)
    connectivity_timeout_seconds: int = Field(default=5, ge=1)

    # Timezone — applied to all scheduled jobs
    timezone: str = "UTC"

    # Quiet hours — notifications classified as NORMAL/LOW are queued during this window
    quiet_hours_start: int = Field(default=22, ge=0, le=23)  # 10pm
    quiet_hours_end: int = Field(default=8, ge=0, le=23)  # 8am

    # Cloud backup
    backup_repo: str = ""

    # Feature flags
    debug_audit: bool = False
