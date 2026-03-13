"""Tests for shared cost rates (src/costs.py)."""

from __future__ import annotations

from src.costs import MODEL_COSTS, cost_rates_per_token, model_cost_usd
from src.models import ModelId


class TestModelCostUsd:
    """model_cost_usd should compute total cost from token counts."""

    def test_known_model_haiku(self) -> None:
        cost = model_cost_usd(ModelId.HAIKU, 1_000_000, 0)
        assert cost == pytest.approx(0.80)

    def test_known_model_sonnet(self) -> None:
        cost = model_cost_usd(ModelId.SONNET, 0, 1_000_000)
        assert cost == pytest.approx(15.00)

    def test_known_model_opus(self) -> None:
        cost = model_cost_usd(ModelId.OPUS, 1_000_000, 1_000_000)
        assert cost == pytest.approx(15.00 + 75.00)

    def test_unknown_model_uses_sonnet_default(self) -> None:
        cost = model_cost_usd("unknown-model", 1_000_000, 0)
        assert cost == pytest.approx(3.00)

    def test_zero_tokens_zero_cost(self) -> None:
        cost = model_cost_usd(ModelId.SONNET, 0, 0)
        assert cost == 0.0

    def test_small_token_counts(self) -> None:
        cost = model_cost_usd(ModelId.SONNET, 100, 50)
        expected = (100 * 3.00 + 50 * 15.00) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_cache_read_reduces_cost(self) -> None:
        """Cache reads should be charged at 0.1x the base input rate."""
        # 1000 total input, 800 from cache read, 200 uncached
        cost = model_cost_usd(ModelId.SONNET, 1000, 0, cache_read_input_tokens=800)
        # 200 * 3.00 + 800 * 3.00 * 0.1 = 600 + 240 = 840 / 1M
        expected = (200 * 3.00 + 800 * 3.00 * 0.1) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_cache_write_increases_cost(self) -> None:
        """Cache writes should be charged at 1.25x the base input rate."""
        cost = model_cost_usd(ModelId.SONNET, 1000, 0, cache_creation_input_tokens=1000)
        expected = (1000 * 3.00 * 1.25) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_cache_mixed(self) -> None:
        """Mixed cache read + write + uncached + output tokens."""
        # 1000 total input: 300 cache write, 500 cache read, 200 uncached
        cost = model_cost_usd(
            ModelId.SONNET,
            1000,
            100,
            cache_creation_input_tokens=300,
            cache_read_input_tokens=500,
        )
        expected = (
            200 * 3.00  # uncached
            + 300 * 3.00 * 1.25  # cache write
            + 500 * 3.00 * 0.1  # cache read
            + 100 * 15.00  # output
        ) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_no_cache_tokens_backward_compatible(self) -> None:
        """Without cache params, should behave identically to before."""
        cost_old = (1000 * 3.00 + 500 * 15.00) / 1_000_000
        cost_new = model_cost_usd(ModelId.SONNET, 1000, 500)
        assert cost_new == pytest.approx(cost_old)


class TestCostRatesPerToken:
    """cost_rates_per_token returns per-token rates matched by substring."""

    def test_haiku_substring_match(self) -> None:
        in_rate, out_rate = cost_rates_per_token(ModelId.HAIKU)
        assert in_rate == pytest.approx(0.80 / 1_000_000)
        assert out_rate == pytest.approx(4.00 / 1_000_000)

    def test_sonnet_substring_match(self) -> None:
        in_rate, out_rate = cost_rates_per_token(ModelId.SONNET)
        assert in_rate == pytest.approx(3.00 / 1_000_000)

    def test_opus_substring_match(self) -> None:
        in_rate, out_rate = cost_rates_per_token("some-opus-variant")
        assert in_rate == pytest.approx(15.00 / 1_000_000)
        assert out_rate == pytest.approx(75.00 / 1_000_000)

    def test_case_insensitive(self) -> None:
        in_rate, _ = cost_rates_per_token(ModelId.SONNET.upper())
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
