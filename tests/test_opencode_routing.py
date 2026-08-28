"""Offline tests for advisory-only Go route ordering."""

from datetime import UTC, datetime

from ask_ai_mcp.opencode_pricing import OpenCodeGoModel
from ask_ai_mcp.opencode_routing import coding_route_orders, review_route_order


def _available() -> dict[str, bool]:
    return {model.value: True for model in OpenCodeGoModel}


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
    assert off_standard[:2] == ["deepseek-v4-flash", "glm-5.3-flash"]
    assert off_advanced == ["deepseek-v4-pro", "glm-5.3", "kimi-k3", "grok-4.6"]
    assert peak_standard[:2] == ["glm-5.3-flash", "deepseek-v4-flash"]
    assert peak_advanced == ["glm-5.3", "deepseek-v4-pro", "kimi-k3", "grok-4.6"]


def test_review_reuses_advanced_order_and_filters_unavailable_models() -> None:
    available = _available()
    available["deepseek-v4-pro"] = False
    assert review_route_order(
        available=available,
        ledger=None,
        priced_at=datetime(2026, 8, 28, 5, 0, tzinfo=UTC),
    ) == ["glm-5.3", "kimi-k3", "grok-4.6"]
