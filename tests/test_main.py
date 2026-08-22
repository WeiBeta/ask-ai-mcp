"""Tests for explicit Core and Full MCP entry-point selection."""

import pytest

from ask_ai_mcp import __main__, server


def test_profile_defaults_to_full(monkeypatch) -> None:
    monkeypatch.delenv("ASK_AI_MCP_PROFILE", raising=False)
    assert __main__.selected_mcp() is server.mcp


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("core", server.core_mcp),
        ("CORE", server.core_mcp),
        ("subagent", server.subagent_mcp),
        ("SUBAGENT", server.subagent_mcp),
        ("perception", server.source_mcp),
        ("SOURCE", server.source_mcp),
        ("h3", server.h3_mcp),
        ("H3", server.h3_mcp),
        ("review", server.review_mcp),
        ("code-review", server.review_mcp),
        ("full", server.mcp),
    ],
)
def test_profile_environment_selects_server(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("ASK_AI_MCP_PROFILE", value)
    assert __main__.selected_mcp() is expected


def test_invalid_profile_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("ASK_AI_MCP_PROFILE", "video-ish")
    with pytest.raises(
        RuntimeError, match="must be core, subagent, perception, h3, review, or full"
    ):
        __main__.selected_mcp()
