"""Pinned OpenCode Go catalog and virtual-USD accounting."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OpenCodeGoModel(StrEnum):
    DSV4_FLASH = "deepseek-v4-flash"
    DSV4_PRO = "deepseek-v4-pro"
    GPT_5_6_LUNA = "gpt-5.6-luna"
    QWEN_3_8_MAX = "qwen3.8-max"


class OpenCodeGoProtocol(StrEnum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"


@dataclass(frozen=True)
class OpenCodeGoPrice:
    protocol: OpenCodeGoProtocol
    input_usd_per_million: float
    output_usd_per_million: float
    cache_read_usd_per_million: float
    cache_write_usd_per_million: float = 0.0
    included_limit_usd: float = 15.0


OPENCODE_GO_PRICING_VERSION = "opencode-go-2026-08-14"
OPENCODE_GO_LIMITS_USD = {"rolling_5h": 12.0, "rolling_7d": 30.0, "rolling_30d": 60.0}
OPENCODE_GO_PRICES = {
    OpenCodeGoModel.DSV4_FLASH: OpenCodeGoPrice(
        OpenCodeGoProtocol.CHAT_COMPLETIONS, 0.14, 0.28, 0.0028, included_limit_usd=60.0
    ),
    OpenCodeGoModel.DSV4_PRO: OpenCodeGoPrice(
        OpenCodeGoProtocol.CHAT_COMPLETIONS, 0.435, 0.87, 0.003625
    ),
    OpenCodeGoModel.GPT_5_6_LUNA: OpenCodeGoPrice(
        OpenCodeGoProtocol.RESPONSES, 0.20, 1.20, 0.02, 0.25
    ),
    OpenCodeGoModel.QWEN_3_8_MAX: OpenCodeGoPrice(
        OpenCodeGoProtocol.ANTHROPIC_MESSAGES, 2.0, 6.0, 0.25, 2.5
    ),
}


def calculate_opencode_go_cost(
    model: OpenCodeGoModel,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Estimate Go virtual spend without double-charging cached input tokens."""

    values = (input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)
    if any(value < 0 for value in values):
        raise ValueError("token counts cannot be negative")
    price = OPENCODE_GO_PRICES[model]
    uncached_input = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    total = (
        uncached_input * price.input_usd_per_million
        + output_tokens * price.output_usd_per_million
        + cache_read_tokens * price.cache_read_usd_per_million
        + cache_write_tokens * price.cache_write_usd_per_million
    ) / 1_000_000
    return round(total, 10)
