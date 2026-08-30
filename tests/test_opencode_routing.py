"""Offline tests for Go route ordering without provider submission."""

from datetime import UTC, datetime

from ask_ai_mcp.models import OpenCodeGoAccountUsage, OpenCodeGoModelAllowance
from ask_ai_mcp.opencode_pricing import (
    OPENCODE_GO_PRICING_EFFECTIVE_AT,
    OPENCODE_GO_PRICING_SOURCE_URL,
    OPENCODE_GO_PRICING_VERSION,
    OpenCodeGoModel,
)
from ask_ai_mcp.opencode_routing import coding_route_orders, review_route_order


def _available() -> dict[str, bool]:
    return {model.value: True for model in OpenCodeGoModel}


def _ledger_with_depleted(*depleted: OpenCodeGoModel) -> OpenCodeGoAccountUsage:
    depleted_values = {model.value for model in depleted}
    return OpenCodeGoAccountUsage(
        account="test-account",
        subscription_id="test-subscription",
        model_allowances=[
            OpenCodeGoModelAllowance(
                model_id=model.value,
                spent_usd=15.0 if model.value in depleted_values else 0.0,
                limit_usd=15.0,
                remaining_usd=0.0 if model.value in depleted_values else 15.0,
                effective_remaining_usd=(0.0 if model.value in depleted_values else 15.0),
            )
            for model in OpenCodeGoModel
        ],
        catalog_version=OPENCODE_GO_PRICING_VERSION,
        catalog_effective_at=OPENCODE_GO_PRICING_EFFECTIVE_AT,
        catalog_source_url=OPENCODE_GO_PRICING_SOURCE_URL,
    )


def test_off_peak_prefers_deepseek_and_peak_prefers_glm_without_submitting() -> None:
    off_standard, off_advanced = coding_route_orders(
        available=_available(),
        ledger=None,
        priced_at=datetime(2026, 8, 28, 5, 0, tzinfo=UTC),
    )
    peak_standard, peak_advanced = coding_route_orders(
        available=_available(),
        ledger=None,
        priced_at=datetime(2026, 8, 28, 6, 0, tzinfo=UTC),
    )
    assert off_standard == ["deepseek-v4-flash", "glm-5.3-flash"]
    assert off_advanced == ["deepseek-v4-pro", "glm-5.3", "kimi-k3", "grok-4.6"]
    assert peak_standard == ["glm-5.3-flash", "deepseek-v4-flash"]
    assert peak_advanced == ["glm-5.3", "deepseek-v4-pro", "kimi-k3", "grok-4.6"]


def test_review_reuses_advanced_order_and_filters_unavailable_models() -> None:
    available = _available()
    available["deepseek-v4-pro"] = False
    assert review_route_order(
        available=available,
        ledger=None,
        priced_at=datetime(2026, 8, 28, 5, 0, tzinfo=UTC),
    ) == ["glm-5.3", "kimi-k3", "grok-4.6"]


def test_review_skips_depleted_models_without_switching_after_submission() -> None:
    assert review_route_order(
        available=_available(),
        ledger=_ledger_with_depleted(
            OpenCodeGoModel.DSV4_PRO,
            OpenCodeGoModel.GLM_5_3,
        ),
        priced_at=datetime(2026, 8, 28, 5, 0, tzinfo=UTC),
    ) == ["kimi-k3", "grok-4.6"]
