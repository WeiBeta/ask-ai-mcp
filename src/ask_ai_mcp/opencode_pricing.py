"""Pinned OpenCode Go catalog, limit metadata, and virtual-USD accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from enum import StrEnum


class OpenCodeGoModel(StrEnum):
    GLM_5_3_FLASH = "glm-5.3-flash"
    GLM_5_3 = "glm-5.3"
    KIMI_K3 = "kimi-k3"
    DSV4_FLASH = "deepseek-v4-flash"
    DSV4_PRO = "deepseek-v4-pro"
    GPT_5_6_LUNA = "gpt-5.6-luna"
    QWEN_3_8_MAX = "qwen3.8-max"


class OpenCodeGoProtocol(StrEnum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"


class OpenCodeGoRateBand(StrEnum):
    STANDARD = "standard"
    OFF_PEAK = "off_peak"
    PEAK = "peak"


@dataclass(frozen=True)
class OpenCodeGoTokenRates:
    input_usd_per_million: float
    output_usd_per_million: float
    cache_read_usd_per_million: float
    cache_write_usd_per_million: float | None


@dataclass(frozen=True)
class OpenCodeGoPrice:
    protocol: OpenCodeGoProtocol
    standard_or_off_peak: OpenCodeGoTokenRates
    included_limit_usd: float
    peak: OpenCodeGoTokenRates | None = None


@dataclass(frozen=True)
class OpenCodeGoCostBreakdown:
    model: OpenCodeGoModel
    band: OpenCodeGoRateBand
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    uncached_input_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    cache_read_cost_usd: float
    cache_write_cost_usd: float | None
    total_cost_usd: float | None
    rates: OpenCodeGoTokenRates
    catalog_version: str
    catalog_effective_at: datetime
    catalog_source_url: str
    estimated: bool = True


OPENCODE_GO_PRICING_VERSION = "opencode-go-2026-08-27"
OPENCODE_GO_PRICING_EFFECTIVE_AT = datetime(2026, 8, 27, tzinfo=UTC)
OPENCODE_GO_PRICING_SOURCE_URL = "https://opencode.ai/docs/go/"
OPENCODE_GO_MODELS_URL = "https://opencode.ai/zen/go/v1/models"
OPENCODE_GO_LIMITS_USD = {"rolling_5h": 12.0, "rolling_7d": 30.0, "rolling_30d": 60.0}


def _rates(
    input_price: float,
    output_price: float,
    cache_read_price: float,
    cache_write_price: float | None = None,
) -> OpenCodeGoTokenRates:
    return OpenCodeGoTokenRates(
        input_usd_per_million=input_price,
        output_usd_per_million=output_price,
        cache_read_usd_per_million=cache_read_price,
        cache_write_usd_per_million=cache_write_price,
    )


OPENCODE_GO_PRICES = {
    OpenCodeGoModel.GLM_5_3_FLASH: OpenCodeGoPrice(
        OpenCodeGoProtocol.CHAT_COMPLETIONS,
        _rates(0.15, 0.50, 0.03),
        included_limit_usd=15.0,
    ),
    OpenCodeGoModel.GLM_5_3: OpenCodeGoPrice(
        OpenCodeGoProtocol.CHAT_COMPLETIONS,
        _rates(1.40, 4.40, 0.26),
        included_limit_usd=15.0,
    ),
    OpenCodeGoModel.KIMI_K3: OpenCodeGoPrice(
        OpenCodeGoProtocol.CHAT_COMPLETIONS,
        _rates(3.00, 15.00, 0.30),
        included_limit_usd=15.0,
    ),
    OpenCodeGoModel.DSV4_PRO: OpenCodeGoPrice(
        OpenCodeGoProtocol.CHAT_COMPLETIONS,
        _rates(0.66, 1.98, 0.022),
        included_limit_usd=15.0,
        peak=_rates(1.32, 3.96, 0.044),
    ),
    OpenCodeGoModel.DSV4_FLASH: OpenCodeGoPrice(
        OpenCodeGoProtocol.CHAT_COMPLETIONS,
        _rates(0.22, 0.66, 0.007),
        included_limit_usd=30.0,
        peak=_rates(0.44, 1.32, 0.014),
    ),
    OpenCodeGoModel.GPT_5_6_LUNA: OpenCodeGoPrice(
        OpenCodeGoProtocol.RESPONSES,
        _rates(0.20, 1.20, 0.02, 0.25),
        included_limit_usd=15.0,
    ),
    OpenCodeGoModel.QWEN_3_8_MAX: OpenCodeGoPrice(
        OpenCodeGoProtocol.ANTHROPIC_MESSAGES,
        _rates(2.0, 6.0, 0.25, 2.5),
        included_limit_usd=15.0,
    ),
}


def is_deepseek_peak(at: datetime) -> bool:
    """Return whether ``at`` falls in an official DeepSeek peak window."""

    utc_instant = at.astimezone(UTC)
    if utc_instant.weekday() >= 5:
        return False
    utc_time = utc_instant.time()
    return time(1) <= utc_time < time(4) or time(6) <= utc_time < time(10)


def rates_for(
    model: OpenCodeGoModel, *, priced_at: datetime
) -> tuple[OpenCodeGoRateBand, OpenCodeGoTokenRates]:
    price = OPENCODE_GO_PRICES[model]
    if price.peak is not None:
        if is_deepseek_peak(priced_at):
            return OpenCodeGoRateBand.PEAK, price.peak
        return OpenCodeGoRateBand.OFF_PEAK, price.standard_or_off_peak
    return OpenCodeGoRateBand.STANDARD, price.standard_or_off_peak


def calculate_opencode_go_cost_breakdown(
    model: OpenCodeGoModel,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    priced_at: datetime | None = None,
) -> OpenCodeGoCostBreakdown:
    """Estimate Go virtual spend while preserving unsupported price components."""

    values = (input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)
    if any(value < 0 for value in values):
        raise ValueError("token counts cannot be negative")
    instant = priced_at or datetime.now(UTC)
    band, rates = rates_for(model, priced_at=instant)
    uncached_input = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    input_cost = uncached_input * rates.input_usd_per_million / 1_000_000
    output_cost = output_tokens * rates.output_usd_per_million / 1_000_000
    cache_read_cost = cache_read_tokens * rates.cache_read_usd_per_million / 1_000_000
    cache_write_cost = (
        None
        if rates.cache_write_usd_per_million is None
        else cache_write_tokens * rates.cache_write_usd_per_million / 1_000_000
    )
    total = None
    if cache_write_tokens == 0 or cache_write_cost is not None:
        total = input_cost + output_cost + cache_read_cost + (cache_write_cost or 0.0)
    return OpenCodeGoCostBreakdown(
        model=model,
        band=band,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        uncached_input_tokens=uncached_input,
        input_cost_usd=round(input_cost, 10),
        output_cost_usd=round(output_cost, 10),
        cache_read_cost_usd=round(cache_read_cost, 10),
        cache_write_cost_usd=(
            round(cache_write_cost, 10) if cache_write_cost is not None else None
        ),
        total_cost_usd=round(total, 10) if total is not None else None,
        rates=rates,
        catalog_version=OPENCODE_GO_PRICING_VERSION,
        catalog_effective_at=OPENCODE_GO_PRICING_EFFECTIVE_AT,
        catalog_source_url=OPENCODE_GO_PRICING_SOURCE_URL,
    )


def calculate_opencode_go_cost(
    model: OpenCodeGoModel,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    priced_at: datetime | None = None,
) -> float:
    """Return a total only when every used price component is supported."""

    result = calculate_opencode_go_cost_breakdown(
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        priced_at=priced_at,
    )
    if result.total_cost_usd is None:
        raise ValueError("OpenCode Go cache-write pricing is unsupported for this model")
    return result.total_cost_usd
