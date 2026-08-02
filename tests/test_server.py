"""Tests for the narrow MCP candidate-operation surface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from ask_ai_mcp import server
from ask_ai_mcp.models import (
    CandidateApprovalCommand,
    CandidateDecision,
    DeepSeekModel,
    ToolBuildSpec,
    ToolCategory,
)


def make_spec() -> ToolBuildSpec:
    return ToolBuildSpec(
        name="synthetic_helper",
        category=ToolCategory.TEST_UTILITY,
        purpose="Build a helper for deterministic synthetic fixture tests.",
        input_contract="No real source files, only synthetic fixture values.",
        output_contract="Synthetic JSON written to the isolated output directory.",
        acceptance_tests=["A stdlib unittest verifies the synthetic result."],
        model=DeepSeekModel.FLASH,
    )


def test_mcp_surface_and_raw_schema_are_narrow() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "usage_status",
        "build_helper_tool",
        "review_tool_candidate",
        "approve_tool_candidate",
    }
    build_schema = by_name["build_helper_tool"].parameters
    assert set(build_schema["properties"]) == {"spec", "allow_pro"}
    assert (
        build_schema["properties"]["spec"]["properties"]["name"]["pattern"] == "^[a-z][a-z0-9_]+$"
    )
    approval_schema = by_name["approve_tool_candidate"].parameters
    capability_items = approval_schema["properties"]["command"]["properties"][
        "allowed_capabilities"
    ]["items"]
    assert set(capability_items["enum"]) == {
        "read_synthetic_inputs",
        "read_copied_inputs",
        "write_dedicated_output",
    }


def test_candidate_operations_require_configured_desktop_identity(monkeypatch) -> None:
    monkeypatch.delenv("ASK_AI_MCP_CLIENT_NAME", raising=False)
    with pytest.raises(RuntimeError, match="must be claude_desktop or codex_desktop"):
        server.build_helper_tool(make_spec())


def test_build_refuses_to_spend_when_runner_is_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("ASK_AI_MCP_CLIENT_NAME", "codex_desktop")
    monkeypatch.setattr(
        server,
        "docker_backend_status",
        lambda: SimpleNamespace(ready=False, reasons=["docker_engine_not_available"]),
    )
    monkeypatch.setattr(
        server,
        "get_lifecycle",
        lambda: (_ for _ in ()).throw(AssertionError("lifecycle must not start")),
    )

    with pytest.raises(RuntimeError, match="docker_engine_not_available"):
        server.build_helper_tool(make_spec())


def test_build_binds_usage_to_configured_client(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeLifecycle:
        def run(self, spec, **kwargs):
            captured["spec"] = spec
            captured.update(kwargs)
            return "review-pending"

    monkeypatch.setenv("ASK_AI_MCP_CLIENT_NAME", "claude_desktop")
    monkeypatch.setattr(
        server,
        "docker_backend_status",
        lambda: SimpleNamespace(ready=True, reasons=[]),
    )
    monkeypatch.setattr(server, "get_lifecycle", lambda: FakeLifecycle())

    result = server.build_helper_tool(make_spec(), allow_pro=False)

    assert result == "review-pending"
    assert captured["client_name"] == "claude_desktop"
    assert captured["allow_pro"] is False


def test_review_tool_only_loads_persisted_review(monkeypatch) -> None:
    job_id = "52efb642-6d4a-42ea-9bbf-da5197360c77"
    repository = SimpleNamespace(load=lambda value: ("review", value))
    monkeypatch.setattr(server, "get_review_repository", lambda: repository)
    assert server.review_tool_candidate(job_id) == ("review", job_id)


def test_approval_identity_comes_from_server_configuration(monkeypatch) -> None:
    job_id = "52efb642-6d4a-42ea-9bbf-da5197360c77"
    candidate_hash = "a" * 64
    captured = {}

    class FakeRegistry:
        def approve(self, **kwargs):
            captured.update(kwargs)
            return Path("registered"), "record"

    monkeypatch.setenv("ASK_AI_MCP_CLIENT_NAME", "codex_desktop")
    monkeypatch.setattr(
        server,
        "get_review_repository",
        lambda: SimpleNamespace(
            load=lambda _job_id: SimpleNamespace(candidate_sha256=candidate_hash)
        ),
    )
    monkeypatch.setattr(
        server,
        "get_workspace",
        lambda: SimpleNamespace(jobs_root=Path("jobs")),
    )
    monkeypatch.setattr(server, "get_registry", lambda: FakeRegistry())

    result = server.approve_tool_candidate(
        CandidateApprovalCommand(
            job_id=job_id,
            candidate_sha256=candidate_hash,
            version="0.1.0",
            allowed_capabilities=["read_synthetic_inputs"],
        )
    )

    assert result == "record"
    request = captured["request"]
    assert request.approved_by == "codex_desktop"
    assert request.decision is CandidateDecision.APPROVED
    assert request.candidate_sha256 == candidate_hash
