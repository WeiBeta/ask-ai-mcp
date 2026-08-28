from __future__ import annotations

from datetime import UTC, datetime

from ask_ai_mcp.accounting import AccountingServices, ApprovalAction, UserApprovalPolicy
from ask_ai_mcp.models import ModelProvider, PricingBand, UsageCostSource, UsageEvent
from ask_ai_mcp.usage import UsageStore


def _event() -> UsageEvent:
    return UsageEvent(
        timestamp=datetime.now(UTC),
        client_name="codex_desktop",
        task_kind="accounting_contract_test",
        model="deepseek-v4-flash",
        provider=ModelProvider.OPENCODE,
        provider_model_id="deepseek-v4-flash",
        provider_runtime="chat_completions",
        provider_account="test-account",
        provider_subscription_id="go-account:test-account",
        thinking_enabled=True,
        reasoning_effort="max",
        max_output_tokens=131_072,
        priced_at=datetime.now(UTC),
        pricing_band=PricingBand.STANDARD,
        pricing_multiplier=1,
        pricing_schedule_version="test",
        cache_hit_price_cny_per_million=0,
        cache_miss_price_cny_per_million=0,
        output_price_cny_per_million=0,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=10,
        completion_tokens=5,
        reasoning_tokens=3,
        estimated_cost_cny=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        estimated_cost_usd=0.01,
        cost_source=UsageCostSource.LOCAL_ESTIMATE,
        latency_ms=100,
        retries=0,
        status="succeeded",
    )


def test_accounting_services_share_one_compatible_store(tmp_path) -> None:
    store = UsageStore(tmp_path / "usage.db")
    accounting = AccountingServices.from_store(store)

    usage_id = accounting.ledger.append(_event())

    assert usage_id > 0
    assert accounting.store is store
    assert accounting.ledger.path == store.path
    assert accounting.ledger.summarize(days=30).total_calls == 1
    assert (
        accounting.entitlements.denial_reason(
            account="test-account",
            model_id="deepseek-v4-flash",
            subscription_id="go-account:test-account",
        )
        is None
    )


def test_user_approval_policy_is_independent_from_subscription_entitlement() -> None:
    selected = UserApprovalPolicy()
    assert (
        selected.evaluate(ApprovalAction.USE_CONFIGURED_SUBSCRIPTION)
        .requires_explicit_authorization
        is False
    )
    for action in (
        ApprovalAction.EXPORT_PRIVATE_SOURCE,
        ApprovalAction.ADD_OR_FUND_SUBSCRIPTION,
        ApprovalAction.EXPAND_ALLOW_LIST,
        ApprovalAction.RETRY_PAID_REQUEST,
    ):
        assert selected.evaluate(action).requires_explicit_authorization is True
