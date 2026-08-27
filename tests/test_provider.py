"""Tests for provider selection that remains independent of MCP profiles."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ask_ai_mcp import deepseek, opencode
from ask_ai_mcp.models import ModelProvider
from ask_ai_mcp.opencode import OpenCodeGoClient
from ask_ai_mcp.provider import (
    ToolsmithProviderError,
    create_toolsmith_client,
    load_toolsmith_provider,
)
from ask_ai_mcp.qwen_toolsmith import LocalQwenToolsmithClient


def test_opencode_is_default_after_direct_deepseek_suspension(monkeypatch) -> None:
    monkeypatch.delenv("ASK_AI_MCP_TOOLSMITH_PROVIDER", raising=False)

    configuration = load_toolsmith_provider()

    assert configuration.provider is ModelProvider.OPENCODE
    assert configuration.requires_budget_gate is False
    assert configuration.local_runtime is False


@pytest.mark.parametrize(
    ("value", "provider", "budget", "local"),
    [
        ("opencode", ModelProvider.OPENCODE, False, False),
        ("LOCAL_QWEN", ModelProvider.LOCAL_QWEN, False, True),
    ],
)
def test_subscription_and_local_providers_do_not_inherit_cny_gate(
    monkeypatch, tmp_path, value, provider, budget, local
) -> None:
    monkeypatch.setenv("ASK_AI_MCP_TOOLSMITH_PROVIDER", value)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    configuration = load_toolsmith_provider()

    assert configuration.provider is provider
    assert configuration.requires_budget_gate is budget
    assert configuration.local_runtime is local
    if provider is ModelProvider.LOCAL_QWEN:
        monkeypatch.setattr(deepseek, "UsageStore", lambda: SimpleNamespace())
        assert isinstance(create_toolsmith_client(configuration), LocalQwenToolsmithClient)
    else:
        monkeypatch.setenv("ASK_AI_MCP_OPENCODE_ACCOUNT_UID", "provider-test")
        monkeypatch.setenv("ASK_AI_MCP_OPENCODE_ACCOUNT_ALIAS", "Provider Test")
        monkeypatch.setattr(opencode, "UsageStore", lambda: SimpleNamespace())
        monkeypatch.setattr(deepseek, "UsageStore", lambda: SimpleNamespace())
        assert isinstance(create_toolsmith_client(configuration), OpenCodeGoClient)


def test_unknown_provider_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("ASK_AI_MCP_TOOLSMITH_PROVIDER", "free-form-endpoint")

    with pytest.raises(ToolsmithProviderError, match="must be"):
        load_toolsmith_provider()


def test_direct_deepseek_is_suspended_unless_explicitly_reactivated(monkeypatch) -> None:
    monkeypatch.setenv("ASK_AI_MCP_TOOLSMITH_PROVIDER", "deepseek")
    monkeypatch.delenv("ASK_AI_MCP_DEEPSEEK_STATE", raising=False)
    with pytest.raises(ToolsmithProviderError, match="suspended"):
        load_toolsmith_provider()

    monkeypatch.setenv("ASK_AI_MCP_DEEPSEEK_STATE", "active")
    assert load_toolsmith_provider().provider is ModelProvider.DEEPSEEK
