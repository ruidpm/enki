"""Tests for shared cost rates (src/costs.py)."""

from __future__ import annotations

from src.costs import MODEL_COSTS, cost_rates_per_token, model_cost_usd


class TestModelCostUsd:
    """model_cost_usd should compute total cost from token counts."""

    def test_known_model_haiku(self) -> None:
        cost = model_cost_usd("claude-haiku-4-5-20251001", 1_000_000, 0)
        assert cost == pytest.approx(0.80)

    def test_known_model_sonnet(self) -> None:
        cost = model_cost_usd("claude-sonnet-4-6", 0, 1_000_000)
        assert cost == pytest.approx(15.00)

    def test_known_model_opus(self) -> None:
        cost = model_cost_usd("claude-opus-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(15.00 + 75.00)

    def test_unknown_model_uses_sonnet_default(self) -> None:
        cost = model_cost_usd("unknown-model", 1_000_000, 0)
        assert cost == pytest.approx(3.00)

    def test_zero_tokens_zero_cost(self) -> None:
        cost = model_cost_usd("claude-sonnet-4-6", 0, 0)
        assert cost == 0.0

    def test_small_token_counts(self) -> None:
        cost = model_cost_usd("claude-sonnet-4-6", 100, 50)
        expected = (100 * 3.00 + 50 * 15.00) / 1_000_000
        assert cost == pytest.approx(expected)


class TestCostRatesPerToken:
    """cost_rates_per_token returns per-token rates matched by substring."""

    def test_haiku_substring_match(self) -> None:
        in_rate, out_rate = cost_rates_per_token("claude-haiku-4-5-20251001")
        assert in_rate == pytest.approx(0.80 / 1_000_000)
        assert out_rate == pytest.approx(4.00 / 1_000_000)

    def test_sonnet_substring_match(self) -> None:
        in_rate, out_rate = cost_rates_per_token("claude-sonnet-4-6")
        assert in_rate == pytest.approx(3.00 / 1_000_000)

    def test_opus_substring_match(self) -> None:
        in_rate, out_rate = cost_rates_per_token("some-opus-variant")
        assert in_rate == pytest.approx(15.00 / 1_000_000)
        assert out_rate == pytest.approx(75.00 / 1_000_000)

    def test_case_insensitive(self) -> None:
        in_rate, _ = cost_rates_per_token("CLAUDE-SONNET-4-6")
        assert in_rate == pytest.approx(3.00 / 1_000_000)

    def test_unknown_model_defaults_to_haiku(self) -> None:
        in_rate, out_rate = cost_rates_per_token("totally-unknown")
        assert in_rate == pytest.approx(0.80 / 1_000_000)
        assert out_rate == pytest.approx(4.00 / 1_000_000)

    def test_empty_string_defaults_to_haiku(self) -> None:
        in_rate, out_rate = cost_rates_per_token("")
        assert in_rate == pytest.approx(0.80 / 1_000_000)
        assert out_rate == pytest.approx(4.00 / 1_000_000)


class TestModelCostsDict:
    """MODEL_COSTS dict should have expected entries."""

    def test_all_models_have_two_element_tuples(self) -> None:
        for model, rates in MODEL_COSTS.items():
            assert len(rates) == 2, f"{model} has {len(rates)} rates"
            assert all(r > 0 for r in rates), f"{model} has non-positive rate"


import pytest  # noqa: E402 — keep imports clean for test discovery
