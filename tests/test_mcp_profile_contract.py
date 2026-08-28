from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from ask_ai_mcp import server
from ask_ai_mcp.mcp_profiles import (
    PROFILE_ALIASES,
    PROFILE_DEFINITIONS,
    Capability,
    ProfileName,
    profiles_for_capability,
)

EXPECTED_PROFILE_CONTRACTS = {
    "full": (
        [
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
        ],
        "1388164ba9341698b48effa780d2d98eb6cc6e4e2ad7a1dea7e9dba7cb685932",
    ),
    "core": (
        [
            "usage_status",
            "workflow_guidance",
            "list_pending_reviews",
            "build_helper_tool",
            "review_tool_candidate",
            "approve_tool_candidate",
            "list_registered_tools",
            "run_verified_tool",
        ],
        "9a33a2f8dd57257168d7fbd563f2f6ae9ec62f9a6178c09fab3fb8828d9398fa",
    ),
    "subagent": (
        [
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
        ],
        "675bef441bf118cf4e438ad344e4a6aafbef36c78eb75c71ac912918cd6645e1",
    ),
    "perception": (
        ["source_backend_status", "source_extract", "source_job_status"],
        "8c86c2788590883b0e3b5a320e74c8dd737f14947037b2ba215153ad13a9fc20",
    ),
    "h3": (
        ["h3_backend_status", "h3_generate_video", "h3_postprocess_video", "h3_job_status"],
        "38f2ab9b9902d4f61bbb0afbe4be06eb119309b7a876a8443b0f7fee0d6a3357",
    ),
    "review": (
        [
            "code_review_backend_status",
            "code_review_stage_patch",
            "code_review_submit",
            "code_review_status",
        ],
        "489cb3674598886c0c0e98c36f2e0d4f03ea97b00a69dca57199ce2ec2f150ab",
    ),
    "coding": (
        ["coding_backend_status", "coding_submit", "coding_status"],
        "e076116d18ae02f831073a3c9f42329671ea7de2c68ff7c9c3dac28385d7846a",
    ),
}


def _surface(server_instance):
    tools = asyncio.run(server_instance.list_tools())
    payload = [{"name": tool.name, "parameters": tool.parameters} for tool in tools]
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return [tool.name for tool in tools], digest


def test_profile_tool_names_and_parameter_schemas_match_0123() -> None:
    servers = {
        "full": server.mcp,
        "core": server.core_mcp,
        "subagent": server.subagent_mcp,
        "perception": server.source_mcp,
        "h3": server.h3_mcp,
        "review": server.review_mcp,
        "coding": server.coding_mcp,
    }
    assert {name: _surface(instance) for name, instance in servers.items()} == (
        EXPECTED_PROFILE_CONTRACTS
    )


def test_capability_memberships_are_explicit_and_disjoint_where_required() -> None:
    assert profiles_for_capability(Capability.TOOLSMITH) == (
        ProfileName.FULL,
        ProfileName.CORE,
        ProfileName.SUBAGENT,
    )
    assert profiles_for_capability(Capability.PERCEPTION) == (
        ProfileName.FULL,
        ProfileName.SUBAGENT,
        ProfileName.PERCEPTION,
    )
    assert profiles_for_capability(Capability.H3) == (ProfileName.FULL, ProfileName.H3)
    assert profiles_for_capability(Capability.CODE_REVIEW) == (ProfileName.REVIEW,)
    assert profiles_for_capability(Capability.CODING) == (ProfileName.CODING,)


def test_profile_and_alias_catalogs_are_immutable() -> None:
    with pytest.raises(TypeError):
        PROFILE_DEFINITIONS[ProfileName.CORE] = PROFILE_DEFINITIONS[ProfileName.FULL]  # type: ignore[index]
    with pytest.raises(TypeError):
        PROFILE_ALIASES["core"] = ProfileName.FULL  # type: ignore[index]
