"""Tests for auditable DeepSeek cost estimates."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from ask_ai_mcp.models import DeepSeekModel, PricingBand
from ask_ai_mcp.pricing import calculate_cost_estimate, estimate_cost_cny


def test_flash_cost_uses_cache_hit_miss_and_output_rates() -> None:
    cost = estimate_cost_cny(
        DeepSeekModel.FLASH,
        prompt_cache_hit_tokens=1_000_000,
        prompt_cache_miss_tokens=1_000_000,
        completion_tokens=1_000_000,
    )
    assert cost == 3.02


def test_pro_cost_uses_current_snapshot() -> None:
    cost = estimate_cost_cny(
        DeepSeekModel.PRO,
        prompt_cache_hit_tokens=1_000_000,
        prompt_cache_miss_tokens=1_000_000,
        completion_tokens=1_000_000,
    )
    assert cost == 9.025


def test_negative_token_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        estimate_cost_cny(
            DeepSeekModel.FLASH,
            prompt_cache_hit_tokens=-1,
            prompt_cache_miss_tokens=0,
            completion_tokens=0,
        )


@pytest.mark.parametrize(
    ("hour", "minute", "expected_band"),
    [
        (8, 59, PricingBand.STANDARD),
        (9, 0, PricingBand.PEAK),
        (11, 59, PricingBand.PEAK),
        (12, 0, PricingBand.STANDARD),
        (13, 59, PricingBand.STANDARD),
        (14, 0, PricingBand.PEAK),
        (17, 59, PricingBand.PEAK),
        (18, 0, PricingBand.STANDARD),
    ],
)
def test_peak_windows_use_beijing_time(hour: int, minute: int, expected_band: PricingBand) -> None:
    beijing_offset = timezone(timedelta(hours=8))
    priced_at = datetime(2026, 8, 3, hour, minute, tzinfo=beijing_offset)
    estimate = calculate_cost_estimate(
        DeepSeekModel.FLASH,
        prompt_cache_hit_tokens=1_000_000,
        prompt_cache_miss_tokens=1_000_000,
        completion_tokens=1_000_000,
        priced_at=priced_at,
        peak_pricing_effective_at=datetime(2026, 8, 1, tzinfo=beijing_offset),
    )

    assert estimate.pricing_band is expected_band
    assert estimate.cost_cny == (6.04 if expected_band is PricingBand.PEAK else 3.02)


def test_pending_peak_schedule_does_not_double_peak_hour() -> None:
    estimate = calculate_cost_estimate(
        DeepSeekModel.PRO,
        prompt_cache_hit_tokens=1_000_000,
        prompt_cache_miss_tokens=1_000_000,
        completion_tokens=1_000_000,
        priced_at=datetime(2026, 8, 3, 2, 0, tzinfo=UTC),
        peak_pricing_effective_at=None,
    )

    assert estimate.peak_pricing_enabled is False
    assert estimate.pricing_band is PricingBand.STANDARD
    assert estimate.pricing_multiplier == 1.0
    assert estimate.cost_cny == 9.025


def test_naive_pricing_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        calculate_cost_estimate(
            DeepSeekModel.FLASH,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=0,
            completion_tokens=0,
            priced_at=datetime(2026, 8, 3, 9, 0),
            peak_pricing_effective_at=None,
        )
