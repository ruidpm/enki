"""Tests for model routing logic."""

from __future__ import annotations

from src.agent import ModelTier, classify_complexity


def test_simple_lookup_routes_to_haiku() -> None:
    assert classify_complexity("what time is it") == ModelTier.HAIKU


def test_task_list_routes_to_haiku() -> None:
    assert classify_complexity("list my tasks") == ModelTier.HAIKU


def test_research_routes_to_sonnet() -> None:
    assert classify_complexity("search for mortgage rate trends and summarize") == ModelTier.SONNET


def test_default_routes_to_sonnet() -> None:
    assert classify_complexity("help me think through this") == ModelTier.SONNET


def test_opus_override_keyword() -> None:
    assert classify_complexity("/opus write a detailed analysis") == ModelTier.OPUS


def test_use_opus_phrase() -> None:
    assert classify_complexity("use opus for this deep dive") == ModelTier.OPUS


def test_complex_planning_routes_to_opus() -> None:
    assert classify_complexity("architect a full migration plan for my database with rollback strategy") == ModelTier.OPUS
