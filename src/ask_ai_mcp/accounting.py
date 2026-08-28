"""Provider-neutral accounting boundaries over the compatible SQLite store."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ask_ai_mcp.models import UsageEvent, UsageSummary
from ask_ai_mcp.usage import UsageStore


@dataclass(frozen=True, slots=True)
class SubscriptionEntitlements:
    """Read-only entitlement checks; remote rate limits remain authoritative."""

    store: UsageStore

    def denial_reason(
        self, *, account: str, model_id: str, subscription_id: str | None
    ) -> str | None:
        return self.store.opencode_limit_reason(account, model_id, subscription_id)


@dataclass(frozen=True, slots=True)
class ImmutableUsageLedger:
    """Append usage events and expose compact summaries without policy decisions."""

    store: UsageStore

    @property
    def path(self) -> Path:
        return self.store.path

    def append(self, event: UsageEvent) -> int:
        return self.store.record(event)

    def summarize(self, *, days: int = 15) -> UsageSummary:
        return self.store.summarize(days=days)


class ApprovalAction(StrEnum):
    USE_CONFIGURED_SUBSCRIPTION = "use_configured_subscription"
    EXPORT_PRIVATE_SOURCE = "export_private_source"
    ADD_OR_FUND_SUBSCRIPTION = "add_or_fund_subscription"
    EXPAND_ALLOW_LIST = "expand_allow_list"
    RETRY_PAID_REQUEST = "retry_paid_request"


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    requires_explicit_authorization: bool
    reason: str


class UserApprovalPolicy:
    """Keep economic entitlement separate from authority to export or retry."""

    _REQUIRES_APPROVAL = frozenset(
        {
            ApprovalAction.EXPORT_PRIVATE_SOURCE,
            ApprovalAction.ADD_OR_FUND_SUBSCRIPTION,
            ApprovalAction.EXPAND_ALLOW_LIST,
            ApprovalAction.RETRY_PAID_REQUEST,
        }
    )

    def evaluate(self, action: ApprovalAction) -> ApprovalDecision:
        required = action in self._REQUIRES_APPROVAL
        return ApprovalDecision(
            requires_explicit_authorization=required,
            reason=(
                "explicit user authorization is required"
                if required
                else "configured subscription may be used within ledger gates"
            ),
        )


@dataclass(frozen=True, slots=True)
class AccountingServices:
    """Composition object; not an MCP process and not a second persistence store."""

    store: UsageStore
    entitlements: SubscriptionEntitlements
    ledger: ImmutableUsageLedger
    approvals: UserApprovalPolicy

    @classmethod
    def from_store(cls, store: UsageStore | None = None) -> AccountingServices:
        selected = store or UsageStore()
        return cls(
            store=selected,
            entitlements=SubscriptionEntitlements(selected),
            ledger=ImmutableUsageLedger(selected),
            approvals=UserApprovalPolicy(),
        )
