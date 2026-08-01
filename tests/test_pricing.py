"""Tests for auditable DeepSeek cost estimates."""

import pytest

from ask_ai_mcp.models import DeepSeekModel
from ask_ai_mcp.pricing import estimate_cost_cny


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
