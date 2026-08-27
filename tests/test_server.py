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
    ReviewMode,
    ToolBuildSpec,
    ToolCategory,
    VerifiedToolExecutionCommand,
    WorkflowGuidanceTopic,
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


def test_full_mcp_surface_and_raw_schema_are_narrow() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "h3_backend_status",
        "h3_generate_video",
        "h3_postprocess_video",
        "h3_job_status",
        "source_backend_status",
        "source_extract",
        "source_job_status",
        "usage_status",
        "workflow_guidance",
        "list_pending_reviews",
        "build_helper_tool",
        "review_tool_candidate",
        "approve_tool_candidate",
        "list_registered_tools",
        "run_verified_tool",
    }
    duration_schema = by_name["h3_generate_video"].parameters["properties"]["command"][
        "properties"
    ]["duration_seconds"]
    assert duration_schema["minimum"] == 4.0
    assert duration_schema["maximum"] == 15.0
    assert "5-15 is recommended" in duration_schema["description"]
    source_schema = by_name["source_extract"].parameters["properties"]["command"]["properties"]
    assert set(source_schema) == {
        "source_files",
        "profile",
        "detail_level",
        "visual_scope",
        "focus_ids",
        "focus_region_xywh",
        "page_start",
        "page_end",
        "language_hint",
    }
    assert set(source_schema["profile"]["enum"]) == {
        "document_evidence",
        "visual_structure",
    }
    assert set(source_schema["visual_scope"]["enum"]) == {
        "structure_index",
        "topology",
        "selected_details",
    }
    assert source_schema["focus_ids"]["maxItems"] == 8
    assert source_schema["focus_region_xywh"]["anyOf"][0]["minItems"] == 4
    assert source_schema["focus_region_xywh"]["anyOf"][0]["maxItems"] == 4
    build_schema = by_name["build_helper_tool"].parameters
    assert set(build_schema["properties"]) == {"spec"}
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
    run_schema = by_name["run_verified_tool"].parameters["properties"]["command"]
    assert set(run_schema["properties"]) == {
        "name",
        "version",
        "candidate_sha256",
        "input_files",
        "parameters_json",
    }
    review_annotations = by_name["review_tool_candidate"].annotations
    assert review_annotations is not None
    assert review_annotations.readOnlyHint is False
    assert review_annotations.idempotentHint is False
    guidance_schema = by_name["workflow_guidance"].parameters
    assert set(guidance_schema["properties"]["topic"]["enum"]) == {
        "overview",
        "usage",
        "build",
        "review",
        "approval",
        "run",
    }
    assert all(len(tool.description or "") < 800 for tool in tools)


def test_core_mcp_surface_excludes_h3_tools() -> None:
    tools = asyncio.run(server.core_mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == {
        "usage_status",
        "workflow_guidance",
        "list_pending_reviews",
        "build_helper_tool",
        "review_tool_candidate",
        "approve_tool_candidate",
        "list_registered_tools",
        "run_verified_tool",
    }


def test_subagent_surface_adds_source_tools_without_h3() -> None:
    names = {tool.name for tool in asyncio.run(server.subagent_mcp.list_tools())}
    core_names = {tool.name for tool in asyncio.run(server.core_mcp.list_tools())}

    assert names == core_names | {
        "source_backend_status",
        "source_extract",
        "source_job_status",
    }
    assert not any(name.startswith("h3_") for name in names)


def test_h3_surface_contains_only_local_video_tools() -> None:
    tools = asyncio.run(server.h3_mcp.list_tools())

    assert {tool.name for tool in tools} == {
        "h3_backend_status",
        "h3_generate_video",
        "h3_postprocess_video",
        "h3_job_status",
    }
    assert all(len(tool.description or "") < 800 for tool in tools)


def test_perception_surface_contains_only_source_tools() -> None:
    tools = asyncio.run(server.source_mcp.list_tools())

    assert {tool.name for tool in tools} == {
        "source_backend_status",
        "source_extract",
        "source_job_status",
    }
    assert all(len(tool.description or "") < 800 for tool in tools)


def test_guidance_and_pending_queue_are_prompt_free_local_reads(monkeypatch) -> None:
    guidance = server.workflow_guidance(WorkflowGuidanceTopic.OVERVIEW)
    assert guidance.topic is WorkflowGuidanceTopic.OVERVIEW
    assert any("final prose" in item for item in guidance.guidance)

    monkeypatch.setattr(
        server,
        "get_review_collaboration",
        lambda: SimpleNamespace(list_pending=lambda: "pending"),
    )
    assert server.list_pending_reviews() == "pending"


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
    monkeypatch.setattr(
        server,
        "get_toolsmith_provider",
        lambda: SimpleNamespace(requires_budget_gate=False),
    )

    result = server.build_helper_tool(make_spec())

    assert result == "review-pending"
    assert captured["client_name"] == "claude_desktop"
    assert "budget_session_id" not in captured
    assert captured["allow_pro"] is False


def test_build_rejects_retired_direct_cny_budget_route(monkeypatch) -> None:
    monkeypatch.setenv("ASK_AI_MCP_CLIENT_NAME", "codex_desktop")
    monkeypatch.setattr(
        server,
        "docker_backend_status",
        lambda: SimpleNamespace(ready=True, reasons=[]),
    )
    monkeypatch.setattr(
        server,
        "get_toolsmith_provider",
        lambda: SimpleNamespace(requires_budget_gate=True),
    )
    monkeypatch.setattr(
        server,
        "get_lifecycle",
        lambda: (_ for _ in ()).throw(AssertionError("lifecycle must not start")),
    )

    with pytest.raises(RuntimeError, match="CNY budget sessions are retired"):
        server.build_helper_tool(make_spec())


def test_review_tool_only_loads_persisted_review(monkeypatch) -> None:
    job_id = "52efb642-6d4a-42ea-9bbf-da5197360c77"
    repository = SimpleNamespace(load_summary=lambda value: ("summary", value))
    monkeypatch.setattr(server, "get_review_repository", lambda: repository)
    monkeypatch.setattr(
        server,
        "get_review_collaboration",
        lambda: SimpleNamespace(enrich_summary=lambda value: value),
    )
    assert server.review_tool_candidate(job_id) == ("summary", job_id)


def test_full_review_records_exact_desktop_attestation(monkeypatch) -> None:
    job_id = "52efb642-6d4a-42ea-9bbf-da5197360c77"
    review = SimpleNamespace(job_id=job_id)
    captured = {}
    monkeypatch.setenv("ASK_AI_MCP_CLIENT_NAME", "claude_desktop")
    monkeypatch.setattr(
        server,
        "get_review_repository",
        lambda: SimpleNamespace(load=lambda value: review if value == job_id else None),
    )
    monkeypatch.setattr(
        server,
        "get_review_attestation_store",
        lambda: SimpleNamespace(
            record=lambda value, **kwargs: captured.update(review=value, **kwargs)
        ),
    )

    assert server.review_tool_candidate(job_id, ReviewMode.FULL) is review
    assert captured == {"review": review, "client_name": "claude_desktop"}


def test_core_surface_has_no_legacy_cny_budget_tools() -> None:
    names = {tool.name for tool in asyncio.run(server.core_mcp.list_tools())}
    assert names.isdisjoint(
        {"open_budget_session", "budget_status", "add_budget_block", "close_budget_session"}
    )


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
            load=lambda _job_id: SimpleNamespace(
                candidate_sha256=candidate_hash,
                attempts=[SimpleNamespace(model=DeepSeekModel.FLASH)],
            )
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


def test_high_risk_approval_requires_full_review_attestation(monkeypatch) -> None:
    job_id = "52efb642-6d4a-42ea-9bbf-da5197360c77"
    candidate_hash = "a" * 64
    review = SimpleNamespace(
        candidate_sha256=candidate_hash,
        attempts=[SimpleNamespace(model=DeepSeekModel.FLASH)],
    )
    captured = {}
    monkeypatch.setenv("ASK_AI_MCP_CLIENT_NAME", "claude_desktop")
    monkeypatch.setattr(
        server,
        "get_review_repository",
        lambda: SimpleNamespace(load=lambda _job_id: review),
    )
    monkeypatch.setattr(
        server,
        "get_review_attestation_store",
        lambda: SimpleNamespace(
            require=lambda value, **kwargs: captured.update(review=value, **kwargs)
        ),
    )
    monkeypatch.setattr(server, "get_workspace", lambda: SimpleNamespace(jobs_root=Path("jobs")))
    monkeypatch.setattr(
        server,
        "get_registry",
        lambda: SimpleNamespace(approve=lambda **kwargs: (Path("registered"), "record")),
    )

    result = server.approve_tool_candidate(
        CandidateApprovalCommand(
            job_id=job_id,
            candidate_sha256=candidate_hash,
            version="0.1.0",
            allowed_capabilities=["write_dedicated_output"],
        )
    )

    assert result == "record"
    assert captured == {"review": review, "client_name": "claude_desktop"}


def test_list_registered_tools_is_local_registry_read(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "get_verified_runner",
        lambda: SimpleNamespace(list_registered_tools=lambda: "tools"),
    )
    assert server.list_registered_tools() == "tools"


def test_verified_execution_binds_client_and_checks_backend(monkeypatch) -> None:
    captured = {}

    class FakeRunner:
        def run(self, command, **kwargs):
            captured["command"] = command
            captured.update(kwargs)
            return "report"

    command = VerifiedToolExecutionCommand(
        name="fixture_counter",
        version="0.1.0",
        candidate_sha256="a" * 64,
    )
    monkeypatch.setenv("ASK_AI_MCP_CLIENT_NAME", "claude_desktop")
    monkeypatch.setattr(
        server,
        "docker_backend_status",
        lambda: SimpleNamespace(ready=True, reasons=[]),
    )
    monkeypatch.setattr(server, "get_verified_runner", lambda: FakeRunner())

    assert server.run_verified_tool(command) == "report"
    assert captured["client_name"] == "claude_desktop"
    assert captured["command"] == command
