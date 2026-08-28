"""Provider selection separated from MCP capability and lifecycle schemas."""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Protocol

from ask_ai_mcp.accounting import AccountingServices
from ask_ai_mcp.deepseek import DeepSeekClient
from ask_ai_mcp.models import ModelProvider, StrictModel
from ask_ai_mcp.opencode import OpenCodeGoClient
from ask_ai_mcp.qwen_toolsmith import LocalQwenToolsmithClient

TOOLSMITH_PROVIDER_ENV = "ASK_AI_MCP_TOOLSMITH_PROVIDER"
DEEPSEEK_STATE_ENV = "ASK_AI_MCP_DEEPSEEK_STATE"


class ToolsmithProviderError(RuntimeError):
    """Raised when a selected provider has no validated adapter yet."""


class WorkerRole(StrEnum):
    """Stable responsibility names; providers and models remain route metadata."""

    TOOLSMITH = "toolsmith"
    CODING = "coding"
    REVIEW = "review"
    PERCEPTION = "perception"
    VIDEO = "video"


class WorkerRouteConfiguration(StrictModel):
    role: WorkerRole = WorkerRole.TOOLSMITH
    provider: ModelProvider = ModelProvider.DEEPSEEK
    requires_budget_gate: bool = True
    local_runtime: bool = False


class ReplayAwareWorker(Protocol):
    @staticmethod
    def replay_prompt_metadata() -> tuple[str, str]: ...


def load_worker_route() -> WorkerRouteConfiguration:
    raw = os.environ.get(TOOLSMITH_PROVIDER_ENV, ModelProvider.OPENCODE.value)
    try:
        provider = ModelProvider(raw.strip().casefold())
    except ValueError as error:
        raise ToolsmithProviderError(
            f"{TOOLSMITH_PROVIDER_ENV} must be deepseek, opencode, or local_qwen"
        ) from error
    if provider is ModelProvider.UNKNOWN:
        raise ToolsmithProviderError(f"{TOOLSMITH_PROVIDER_ENV} cannot be unknown")
    if provider is ModelProvider.DEEPSEEK:
        state = os.environ.get(DEEPSEEK_STATE_ENV, "suspended").strip().casefold()
        if state != "active":
            raise ToolsmithProviderError(
                "DeepSeek direct API is suspended; select the OpenCode provider or explicitly "
                f"set {DEEPSEEK_STATE_ENV}=active after funding the direct account"
            )
    return WorkerRouteConfiguration(
        provider=provider,
        requires_budget_gate=provider is ModelProvider.DEEPSEEK,
        local_runtime=provider is ModelProvider.LOCAL_QWEN,
    )


def create_toolsmith_worker(
    configuration: WorkerRouteConfiguration | None = None,
    *,
    accounting: AccountingServices | None = None,
):
    selected = configuration or load_worker_route()
    if selected.provider is ModelProvider.DEEPSEEK:
        return DeepSeekClient(accounting=accounting)
    if selected.provider is ModelProvider.LOCAL_QWEN:
        return LocalQwenToolsmithClient(accounting=accounting)
    if selected.provider is ModelProvider.OPENCODE:
        return OpenCodeGoClient(accounting=accounting)
    raise ToolsmithProviderError(
        f"{selected.provider.value} toolsmith adapter awaits validated runtime configuration"
    )


# Compatibility aliases for 0.12.x internal imports. New composition code uses
# role/worker terminology so executable profiles never become provider identities.
ToolsmithProviderConfiguration = WorkerRouteConfiguration
CandidateProvider = ReplayAwareWorker
load_toolsmith_provider = load_worker_route
create_toolsmith_client = create_toolsmith_worker
