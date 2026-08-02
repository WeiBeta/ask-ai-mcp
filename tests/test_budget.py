"""Tests for per-conversation five-yuan budget grants."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ask_ai_mcp.budget import BudgetAuthorizationError, BudgetError, BudgetStore
from ask_ai_mcp.models import (
    BudgetState,
    DeepSeekModel,
    PricingBand,
    UsageEvent,
)
from ask_ai_mcp.usage import UsageStore


def usage_event(session_id: str, *, model: DeepSeekModel, cost: float) -> UsageEvent:
    return UsageEvent(
        client_name="codex_desktop",
        task_kind="tool_build",
        model=model,
        thinking_enabled=True,
        priced_at=datetime.now(UTC),
        pricing_band=PricingBand.STANDARD,
        pricing_multiplier=1.0,
        pricing_schedule_version="test",
        estimated_cost_cny=cost,
        status="success",
        budget_session_id=session_id,
        lifecycle_id="11111111-1111-4111-8111-111111111111",
    )


def test_new_session_starts_with_flash_five_and_pro_zero(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "usage.db")

    status = store.open_session(client_name="codex_desktop", label="test chat")

    assert status.flash.state is BudgetState.ACTIVE
    assert status.flash.granted_cny == 5.0
    assert status.pro.state is BudgetState.PRO_AUTHORIZATION_REQUIRED
    assert status.pro.granted_cny == 0.0
    assert status.lifecycle_count == 0
    assert status.api_call_count == 0


def test_pro_budget_is_added_only_in_fixed_five_yuan_blocks(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "usage.db")
    opened = store.open_session(client_name="claude_desktop")

    updated = store.add_budget_block(
        opened.budget_session_id,
        client_name="claude_desktop",
        model=DeepSeekModel.PRO,
    )

    assert updated.pro.state is BudgetState.ACTIVE
    assert updated.pro.granted_cny == 5.0
    assert updated.flash.granted_cny == 5.0
    with pytest.raises(BudgetError, match="authorization or extension"):
        store.add_budget_block(
            opened.budget_session_id,
            client_name="claude_desktop",
            model=DeepSeekModel.PRO,
        )


def test_active_flash_budget_cannot_be_prefunded(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "usage.db")
    opened = store.open_session(client_name="codex_desktop")

    with pytest.raises(BudgetError, match="authorization or extension"):
        store.add_budget_block(
            opened.budget_session_id,
            client_name="codex_desktop",
            model=DeepSeekModel.FLASH,
        )


def test_spend_can_cross_boundary_but_next_lifecycle_is_blocked(tmp_path: Path) -> None:
    database = tmp_path / "usage.db"
    budgets = BudgetStore(database)
    opened = budgets.open_session(client_name="codex_desktop")
    UsageStore(database).record(
        usage_event(opened.budget_session_id, model=DeepSeekModel.FLASH, cost=5.01)
    )

    status = budgets.status(opened.budget_session_id, client_name="codex_desktop")
    assert status.flash.state is BudgetState.FLASH_EXTENSION_REQUIRED
    assert status.flash.overshoot_cny == 0.01
    with pytest.raises(BudgetAuthorizationError, match="flash_extension_required"):
        budgets.require_lifecycle_budget(
            opened.budget_session_id,
            client_name="codex_desktop",
            model=DeepSeekModel.FLASH,
        )

    extended = budgets.add_budget_block(
        opened.budget_session_id,
        client_name="codex_desktop",
        model=DeepSeekModel.FLASH,
    )
    assert extended.flash.state is BudgetState.ACTIVE
    assert extended.flash.granted_cny == 10.0
    assert extended.flash.remaining_cny == 4.99


def test_budget_session_is_bound_to_desktop_identity(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "usage.db")
    opened = store.open_session(client_name="codex_desktop")

    with pytest.raises(BudgetError, match="different desktop"):
        store.status(opened.budget_session_id, client_name="claude_desktop")


def test_closed_session_cannot_be_used_or_extended(tmp_path: Path) -> None:
    store = BudgetStore(tmp_path / "usage.db")
    opened = store.open_session(client_name="codex_desktop")
    closed = store.close_session(opened.budget_session_id, client_name="codex_desktop")

    assert closed.flash.state is BudgetState.CLOSED
    assert closed.pro.state is BudgetState.CLOSED
    with pytest.raises(BudgetAuthorizationError, match="closed"):
        store.require_lifecycle_budget(
            opened.budget_session_id,
            client_name="codex_desktop",
            model=DeepSeekModel.FLASH,
        )
    with pytest.raises(BudgetError, match="closed"):
        store.add_budget_block(
            opened.budget_session_id,
            client_name="codex_desktop",
            model=DeepSeekModel.FLASH,
        )
