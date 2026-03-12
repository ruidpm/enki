"""Canonical model identifiers — single source of truth.

When Anthropic ships a new model version, update ONLY this file.
Everything else (config defaults, cost tables, tests) references these enums.
"""

from __future__ import annotations

from enum import StrEnum


class ModelId(StrEnum):
    """Exact API model identifier strings."""

    HAIKU = "claude-haiku-4-5-20251001"
    SONNET = "claude-sonnet-4-6"
    OPUS = "claude-opus-4-6"
