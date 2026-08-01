"""DeepSeek usage-price calculations for audit estimates."""

from __future__ import annotations

from dataclasses import dataclass

from ask_ai_mcp.models import DeepSeekModel


@dataclass(frozen=True, slots=True)
class TokenPrices:
    cache_hit_input: float
    cache_miss_input: float
    output: float


PRICES_CNY_PER_MILLION = {
    DeepSeekModel.FLASH: TokenPrices(0.02, 1.0, 2.0),
    DeepSeekModel.PRO: TokenPrices(0.025, 3.0, 6.0),
}
PRICE_SNAPSHOT_DATE = "2026-08-01"


def estimate_cost_cny(
    model: DeepSeekModel,
    *,
    prompt_cache_hit_tokens: int,
    prompt_cache_miss_tokens: int,
    completion_tokens: int,
) -> float:
    """Estimate one call from the configured price snapshot."""
    counts = (prompt_cache_hit_tokens, prompt_cache_miss_tokens, completion_tokens)
    if any(count < 0 for count in counts):
        raise ValueError("token counts must be non-negative")

    prices = PRICES_CNY_PER_MILLION[model]
    cost = (
        prompt_cache_hit_tokens * prices.cache_hit_input
        + prompt_cache_miss_tokens * prices.cache_miss_input
        + completion_tokens * prices.output
    ) / 1_000_000
    return round(cost, 8)
