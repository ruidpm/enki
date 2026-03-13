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


def model_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    """Calculate cost in USD for a given model and token counts.

    When cache tokens are provided, ``input_tokens`` from the API already
    includes them in the total.  We subtract the cached portions and re-price
    them at their actual rates (1.25x for writes, 0.1x for reads).
    """
    in_rate, out_rate = MODEL_COSTS.get(model, _DEFAULT_RATES)
    uncached = input_tokens - cache_creation_input_tokens - cache_read_input_tokens
    return (
        uncached * in_rate
        + cache_creation_input_tokens * in_rate * 1.25
        + cache_read_input_tokens * in_rate * 0.1
        + output_tokens * out_rate
    ) / 1_000_000


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
