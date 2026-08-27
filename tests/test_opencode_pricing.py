"""Pinned official OpenCode Go pricing and dual-limit accounting tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ask_ai_mcp.models import ModelProvider, UsageEvent
from ask_ai_mcp.opencode_pricing import (
    OPENCODE_GO_PRICES,
    OPENCODE_GO_PRICING_EFFECTIVE_AT,
    OPENCODE_GO_PRICING_SOURCE_URL,
    OPENCODE_GO_PRICING_VERSION,
    OpenCodeGoModel,
    OpenCodeGoRateBand,
    calculate_opencode_go_cost,
    calculate_opencode_go_cost_breakdown,
    rates_for,
)
from ask_ai_mcp.usage import UsageStore


def test_catalog_matches_2026_08_27_official_fixed_values() -> None:
    assert OPENCODE_GO_PRICING_VERSION == "opencode-go-2026-08-27"
    assert datetime(2026, 8, 27, tzinfo=UTC) == OPENCODE_GO_PRICING_EFFECTIVE_AT
    assert OPENCODE_GO_PRICING_SOURCE_URL == "https://opencode.ai/docs/go/"
    expected = {
        OpenCodeGoModel.GLM_5_3_FLASH: (0.15, 0.50, 0.03, 15.0),
        OpenCodeGoModel.GLM_5_3: (1.40, 4.40, 0.26, 15.0),
        OpenCodeGoModel.KIMI_K3: (3.00, 15.00, 0.30, 15.0),
        OpenCodeGoModel.DSV4_PRO: (0.66, 1.98, 0.022, 15.0),
        OpenCodeGoModel.DSV4_FLASH: (0.22, 0.66, 0.007, 30.0),
    }
    for model, values in expected.items():
        price = OPENCODE_GO_PRICES[model]
        rates = price.standard_or_off_peak
        assert (
            rates.input_usd_per_million,
            rates.output_usd_per_million,
            rates.cache_read_usd_per_million,
            price.included_limit_usd,
        ) == values
        assert rates.cache_write_usd_per_million is None


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 8, 21, 0, 59, 59, tzinfo=UTC), OpenCodeGoRateBand.OFF_PEAK),
        (datetime(2026, 8, 21, 1, 0, tzinfo=UTC), OpenCodeGoRateBand.PEAK),
        (datetime(2026, 8, 21, 3, 59, 59, tzinfo=UTC), OpenCodeGoRateBand.PEAK),
        (datetime(2026, 8, 21, 4, 0, tzinfo=UTC), OpenCodeGoRateBand.OFF_PEAK),
        (datetime(2026, 8, 21, 6, 0, tzinfo=UTC), OpenCodeGoRateBand.PEAK),
        (datetime(2026, 8, 21, 10, 0, tzinfo=UTC), OpenCodeGoRateBand.OFF_PEAK),
    ],
)
def test_deepseek_peak_windows_are_half_open(instant: datetime, expected) -> None:
    band, _ = rates_for(OpenCodeGoModel.DSV4_FLASH, priced_at=instant)
    assert band is expected


def test_deepseek_weekends_are_always_off_peak() -> None:
    band, _ = rates_for(
        OpenCodeGoModel.DSV4_FLASH,
        priced_at=datetime(2026, 8, 22, 6, 30, tzinfo=UTC),
    )
    assert band is OpenCodeGoRateBand.OFF_PEAK


def test_peak_cost_is_twice_off_peak_and_cache_write_is_unsupported() -> None:
    off_peak = calculate_opencode_go_cost(
        OpenCodeGoModel.DSV4_PRO,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=0,
        priced_at=datetime(2026, 8, 21, 5, 0, tzinfo=UTC),
    )
    peak = calculate_opencode_go_cost(
        OpenCodeGoModel.DSV4_PRO,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=0,
        priced_at=datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
    )
    assert peak == off_peak * 2

    breakdown = calculate_opencode_go_cost_breakdown(
        OpenCodeGoModel.GLM_5_3,
        input_tokens=100,
        output_tokens=20,
        cache_write_tokens=10,
        priced_at=datetime(2026, 8, 21, 5, 0, tzinfo=UTC),
    )
    assert breakdown.rates.cache_write_usd_per_million is None
    assert breakdown.cache_write_cost_usd is None
    assert breakdown.total_cost_usd is None
    with pytest.raises(ValueError, match="unsupported"):
        calculate_opencode_go_cost(
            OpenCodeGoModel.GLM_5_3,
            input_tokens=100,
            output_tokens=20,
            cache_write_tokens=10,
            priced_at=datetime(2026, 8, 21, 5, 0, tzinfo=UTC),
        )


def _usage(
    model: OpenCodeGoModel,
    cost: float,
    *,
    account: str,
    subscription: str,
    age: timedelta = timedelta(days=10),
    provider_reported_cost: float | None = None,
) -> UsageEvent:
    return UsageEvent(
        timestamp=datetime.now(UTC) - age,
        client_name="codex",
        task_kind="code_review",
        model=model.value,
        provider=ModelProvider.OPENCODE,
        provider_model_id=model.value,
        provider_runtime="chat_completions",
        provider_account=account,
        provider_subscription_id=subscription,
        thinking_enabled=True,
        status="success",
        estimated_cost_usd=cost,
        provider_reported_cost_usd=provider_reported_cost,
    )


def test_shared_monthly_spend_is_not_duplicated_across_models(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.db")
    for model in (
        OpenCodeGoModel.GLM_5_3,
        OpenCodeGoModel.KIMI_K3,
        OpenCodeGoModel.DSV4_PRO,
    ):
        store.record(_usage(model, 15.0, account="team_a", subscription="go-2026-a"))

    account = store.summarize(days=30).opencode_go_accounts[0]
    monthly = next(window for window in account.windows if window.window == "rolling_30d")
    flash = next(
        allowance
        for allowance in account.model_allowances
        if allowance.model_id == OpenCodeGoModel.DSV4_FLASH.value
    )
    assert monthly.spent_usd == 45.0
    assert monthly.remaining_usd == 15.0
    assert flash.limit_usd == 30.0
    assert flash.remaining_usd == 30.0
    assert flash.effective_remaining_usd == 15.0


def test_accounts_and_subscriptions_have_isolated_windows(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.db")
    store.record(_usage(OpenCodeGoModel.GLM_5_3, 15.0, account="team", subscription="go-a"))
    store.record(_usage(OpenCodeGoModel.KIMI_K3, 7.0, account="team", subscription="go-b"))

    accounts = store.summarize(days=30).opencode_go_accounts
    assert {(item.account, item.subscription_id) for item in accounts} == {
        ("team", "go-a"),
        ("team", "go-b"),
    }
    spent = {
        item.subscription_id: next(
            window.spent_usd for window in item.windows if window.window == "rolling_30d"
        )
        for item in accounts
    }
    assert spent == {"go-a": 15.0, "go-b": 7.0}


def test_shared_windows_and_reported_cost_are_displayed_without_double_counting(
    tmp_path: Path,
) -> None:
    store = UsageStore(tmp_path / "usage.db")
    store.record(
        _usage(
            OpenCodeGoModel.DSV4_FLASH,
            3.0,
            provider_reported_cost=2.5,
            account="team",
            subscription="go-a",
            age=timedelta(),
        )
    )
    store.record(
        _usage(
            OpenCodeGoModel.GLM_5_3,
            4.0,
            account="team",
            subscription="go-a",
            age=timedelta(days=2),
        )
    )
    account = store.summarize(days=30).opencode_go_accounts[0]
    five_hour = next(item for item in account.windows if item.window == "rolling_5h")
    weekly = next(item for item in account.windows if item.window == "rolling_7d")
    assert five_hour.spent_usd == 2.5
    assert five_hour.estimated_spent_usd == 3.0
    assert five_hour.provider_reported_spent_usd == 2.5
    assert five_hour.provider_reported_call_count == 1
    assert five_hour.total_call_count == 1
    assert account.current_utc_day_spent_usd == 2.5
    assert account.daily_pace_target_usd == 2.0
    assert account.above_daily_pace is True
    assert account.daily_pace_is_hard_limit is False
    assert weekly.spent_usd == 6.5
    assert weekly.estimated_spent_usd == 7.0
