"""DeepSeek usage-price calculations for audit estimates."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone

from ask_ai_mcp.models import DeepSeekModel, PricingBand


@dataclass(frozen=True, slots=True)
class TokenPrices:
    cache_hit_input: float
    cache_miss_input: float
    output: float


BASE_PRICES_CNY_PER_MILLION = {
    DeepSeekModel.FLASH: TokenPrices(0.02, 1.0, 2.0),
    DeepSeekModel.PRO: TokenPrices(0.025, 3.0, 6.0),
}
PRICES_CNY_PER_MILLION = BASE_PRICES_CNY_PER_MILLION
PRICE_SNAPSHOT_DATE = "2026-08-01"
BASE_PRICE_SCHEDULE_VERSION = "deepseek-v4-base-2026-08-01"
PEAK_PRICE_SCHEDULE_VERSION = "deepseek-v4-peak-v1"
PEAK_PRICING_EFFECTIVE_AT_ENV = "ASK_AI_MCP_PEAK_PRICING_EFFECTIVE_AT"
BEIJING_TIME_ZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
PEAK_WINDOWS = ((time(9), time(12)), (time(14), time(18)))


@dataclass(frozen=True, slots=True)
class CostEstimate:
    cost_cny: float
    priced_at: datetime
    beijing_time: datetime
    pricing_band: PricingBand
    pricing_multiplier: float
    pricing_schedule_version: str
    peak_pricing_enabled: bool
    prices: TokenPrices


def load_peak_pricing_effective_at() -> datetime | None:
    """Load an explicit effective instant; an unset value keeps peak pricing off."""
    raw_value = os.environ.get(PEAK_PRICING_EFFECTIVE_AT_ENV, "").strip()
    if not raw_value:
        return None
    try:
        effective_at = datetime.fromisoformat(raw_value)
    except ValueError as error:
        raise ValueError(f"{PEAK_PRICING_EFFECTIVE_AT_ENV} must be an ISO-8601 datetime") from error
    if effective_at.tzinfo is None or effective_at.utcoffset() is None:
        raise ValueError(f"{PEAK_PRICING_EFFECTIVE_AT_ENV} must include a UTC offset")
    return effective_at.astimezone(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("pricing timestamps must include a UTC offset")
    return value.astimezone(UTC)


def pricing_context(
    priced_at: datetime,
    *,
    peak_pricing_effective_at: datetime | None,
) -> tuple[datetime, PricingBand, float, str, bool]:
    """Resolve the auditable Beijing pricing band for one request start."""
    priced_at_utc = _aware_utc(priced_at)
    peak_enabled = False
    if peak_pricing_effective_at is not None:
        effective_at_utc = _aware_utc(peak_pricing_effective_at)
        peak_enabled = priced_at_utc >= effective_at_utc

    beijing_time = priced_at_utc.astimezone(BEIJING_TIME_ZONE)
    in_peak_window = any(
        start <= beijing_time.time().replace(tzinfo=None) < end for start, end in PEAK_WINDOWS
    )
    band = PricingBand.PEAK if peak_enabled and in_peak_window else PricingBand.STANDARD
    multiplier = 2.0 if band is PricingBand.PEAK else 1.0
    schedule_version = PEAK_PRICE_SCHEDULE_VERSION if peak_enabled else BASE_PRICE_SCHEDULE_VERSION
    return beijing_time, band, multiplier, schedule_version, peak_enabled


def calculate_cost_estimate(
    model: DeepSeekModel,
    *,
    prompt_cache_hit_tokens: int,
    prompt_cache_miss_tokens: int,
    completion_tokens: int,
    priced_at: datetime,
    peak_pricing_effective_at: datetime | None,
) -> CostEstimate:
    """Calculate one call and preserve the exact price decision used."""
    counts = (prompt_cache_hit_tokens, prompt_cache_miss_tokens, completion_tokens)
    if any(count < 0 for count in counts):
        raise ValueError("token counts must be non-negative")

    priced_at_utc = _aware_utc(priced_at)
    beijing_time, band, multiplier, schedule_version, peak_enabled = pricing_context(
        priced_at_utc,
        peak_pricing_effective_at=peak_pricing_effective_at,
    )
    base_prices = BASE_PRICES_CNY_PER_MILLION[model]
    prices = TokenPrices(
        cache_hit_input=base_prices.cache_hit_input * multiplier,
        cache_miss_input=base_prices.cache_miss_input * multiplier,
        output=base_prices.output * multiplier,
    )
    cost = (
        prompt_cache_hit_tokens * prices.cache_hit_input
        + prompt_cache_miss_tokens * prices.cache_miss_input
        + completion_tokens * prices.output
    ) / 1_000_000
    return CostEstimate(
        cost_cny=round(cost, 8),
        priced_at=priced_at_utc,
        beijing_time=beijing_time,
        pricing_band=band,
        pricing_multiplier=multiplier,
        pricing_schedule_version=schedule_version,
        peak_pricing_enabled=peak_enabled,
        prices=prices,
    )


def estimate_cost_cny(
    model: DeepSeekModel,
    *,
    prompt_cache_hit_tokens: int,
    prompt_cache_miss_tokens: int,
    completion_tokens: int,
    priced_at: datetime | None = None,
    peak_pricing_effective_at: datetime | None = None,
) -> float:
    """Return the cost number while preserving the existing public helper API."""
    estimate = calculate_cost_estimate(
        model,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
        completion_tokens=completion_tokens,
        priced_at=priced_at or datetime.now(UTC),
        peak_pricing_effective_at=peak_pricing_effective_at,
    )
    return estimate.cost_cny
