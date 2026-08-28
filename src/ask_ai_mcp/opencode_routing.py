"""Local, advisory-only OpenCode Go route ordering."""

from __future__ import annotations

from datetime import UTC, datetime

from ask_ai_mcp.models import OpenCodeGoAccountUsage
from ask_ai_mcp.opencode_pricing import OpenCodeGoModel, is_deepseek_peak

OPENCODE_ROUTING_POLICY_VERSION = "opencode-go-routing-v1"


def _usable_order(
    order: tuple[OpenCodeGoModel, ...],
    *,
    available: dict[str, bool],
    ledger: OpenCodeGoAccountUsage | None,
) -> list[str]:
    remaining = (
        {item.model_id: item.effective_remaining_usd for item in ledger.model_allowances}
        if ledger is not None
        else {}
    )
    return [
        model.value
        for model in order
        if available.get(model.value, False) and remaining.get(model.value, 1.0) > 0
    ]


def coding_route_orders(
    *,
    available: dict[str, bool],
    ledger: OpenCodeGoAccountUsage | None,
    priced_at: datetime | None = None,
) -> tuple[list[str], list[str]]:
    """Return advisory standard and advanced orders without submitting or retrying."""

    instant = priced_at or datetime.now(UTC)
    standard_primary = (
        (OpenCodeGoModel.GLM_5_3_FLASH, OpenCodeGoModel.DSV4_FLASH)
        if is_deepseek_peak(instant)
        else (OpenCodeGoModel.DSV4_FLASH, OpenCodeGoModel.GLM_5_3_FLASH)
    )
    advanced_primary = (
        (OpenCodeGoModel.GLM_5_3, OpenCodeGoModel.DSV4_PRO)
        if is_deepseek_peak(instant)
        else (OpenCodeGoModel.DSV4_PRO, OpenCodeGoModel.GLM_5_3)
    )
    fallbacks = (OpenCodeGoModel.KIMI_K3, OpenCodeGoModel.GROK_4_6)
    return (
        _usable_order(standard_primary, available=available, ledger=ledger),
        _usable_order(advanced_primary + fallbacks, available=available, ledger=ledger),
    )


def review_route_order(
    *,
    available: dict[str, bool],
    ledger: OpenCodeGoAccountUsage | None,
    priced_at: datetime | None = None,
) -> list[str]:
    """Return the advisory Review order; the caller must still select one model explicitly."""

    return coding_route_orders(available=available, ledger=ledger, priced_at=priced_at)[1]
