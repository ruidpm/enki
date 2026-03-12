"""Shared model cost rates — single source of truth for all cost calculations."""

from __future__ import annotations

from src.models import ModelId

# Cost per million tokens (input, output) in USD — approximate
MODEL_COSTS: dict[str, tuple[float, float]] = {
    ModelId.HAIKU: (0.80, 4.00),
    ModelId.SONNET: (3.00, 15.00),
    ModelId.OPUS: (15.00, 75.00),
}

# Default fallback (sonnet rates)
_DEFAULT_RATES: tuple[float, float] = (3.00, 15.00)


def model_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for a given model and token counts."""
    in_rate, out_rate = MODEL_COSTS.get(model, _DEFAULT_RATES)
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def cost_rates_per_token(model: str) -> tuple[float, float]:
    """Return (input_rate, output_rate) per token for JobRegistry-style accounting.

    Falls back to haiku rates for unknown models (cheapest = safest default).
    """
    m = model.lower()
    # Match by substring: "haiku", "sonnet", "opus"
    _RATES_BY_SUBSTRING: dict[str, tuple[float, float]] = {
        "haiku": (0.80, 4.00),
        "sonnet": (3.00, 15.00),
        "opus": (15.00, 75.00),
    }
    for key, (in_rate, out_rate) in _RATES_BY_SUBSTRING.items():
        if key in m:
            return in_rate / 1_000_000, out_rate / 1_000_000
    # Default to haiku (cheapest) for unknown models
    return 0.80 / 1_000_000, 4.00 / 1_000_000
