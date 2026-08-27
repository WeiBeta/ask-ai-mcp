"""Static guarantees for the use-only migration package inputs."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "migration"

CORE_TOOLS = {
    "usage_status",
    "workflow_guidance",
    "list_pending_reviews",
    "build_helper_tool",
    "review_tool_candidate",
    "approve_tool_candidate",
    "list_registered_tools",
    "run_verified_tool",
}
H3_TOOLS = {
    "h3_backend_status",
    "h3_generate_video",
    "h3_postprocess_video",
    "h3_job_status",
}
SOURCE_TOOLS = {
    "source_backend_status",
    "source_extract",
    "source_job_status",
}
REVIEW_TOOLS = {
    "code_review_backend_status",
    "code_review_submit",
    "code_review_status",
}


def test_core_templates_expose_only_core_entrypoint_and_tools() -> None:
    codex = (MIGRATION / "profiles/core/config-templates/codex.toml").read_text("utf-8")
    claude = json.loads(
        (MIGRATION / "profiles/core/config-templates/claude.json").read_text("utf-8")
    )
    assert "__MCP_CORE_EXE__" in codex
    assert claude["mcpServers"]["ask-ai"]["command"] == "__MCP_CORE_EXE__"
    assert all(tool in codex for tool in CORE_TOOLS)
    assert all(tool not in codex for tool in H3_TOOLS)
    assert "ASK_AI_MCP_H3_" not in codex
    assert "ASK_AI_MCP_H3_" not in json.dumps(claude)


def test_full_templates_reference_adapter_but_no_comfyui_payload() -> None:
    codex = (MIGRATION / "profiles/full/config-templates/codex.toml").read_text("utf-8")
    claude = json.loads(
        (MIGRATION / "profiles/full/config-templates/claude.json").read_text("utf-8")
    )
    assert "__MCP_FULL_EXE__" in codex
    assert all(tool in codex for tool in CORE_TOOLS | SOURCE_TOOLS | H3_TOOLS)
    assert "ASK_AI_MCP_H3_URL" in codex
    assert 'ASK_AI_MCP_H3_START_SCRIPT = "__H3_START_SCRIPT__"' in codex
    assert claude["mcpServers"]["ask-ai"]["env"]["ASK_AI_MCP_H3_START_SCRIPT"] == (
        "__H3_START_SCRIPT__"
    )
    package_inputs = [path.name.casefold() for path in MIGRATION.rglob("*") if path.is_file()]
    assert not any(name.endswith((".safetensors", ".pth", ".ckpt")) for name in package_inputs)


def test_subagent_templates_add_only_source_tools_and_qwen_boundary() -> None:
    codex = (MIGRATION / "profiles/subagent/config-templates/codex.toml").read_text("utf-8")
    claude = json.loads(
        (MIGRATION / "profiles/subagent/config-templates/claude.json").read_text("utf-8")
    )
    assert "__MCP_SUBAGENT_EXE__" in codex
    assert all(tool in codex for tool in CORE_TOOLS | SOURCE_TOOLS)
    assert all(tool not in codex for tool in H3_TOOLS)
    assert 'ASK_AI_MCP_SOURCE_PROVIDER = "local_qwen"' in codex
    assert (
        claude["mcpServers"]["ask-ai-subagent"]["env"]["ASK_AI_MCP_SOURCE_INPUT_ROOTS"]
        == "__SOURCE_INPUT_ROOTS__"
    )


def test_h3_templates_are_standalone_and_disabled_by_default_for_codex() -> None:
    codex = (MIGRATION / "profiles/h3/config-templates/codex.toml").read_text("utf-8")
    claude = json.loads((MIGRATION / "profiles/h3/config-templates/claude.json").read_text("utf-8"))
    assert "__MCP_H3_EXE__" in codex
    assert "enabled = false" in codex
    assert all(tool in codex for tool in H3_TOOLS)
    assert all(tool not in codex for tool in CORE_TOOLS | SOURCE_TOOLS)
    assert claude["mcpServers"]["ask-ai-h3"]["command"] == "__MCP_H3_EXE__"


def test_review_templates_are_standalone_and_not_part_of_default_profiles() -> None:
    codex = (MIGRATION / "profiles/review/config-templates/codex.toml").read_text("utf-8")
    claude = json.loads(
        (MIGRATION / "profiles/review/config-templates/claude.json").read_text("utf-8")
    )
    assert "__MCP_REVIEW_EXE__" in codex
    assert "enabled = false" in codex
    assert all(tool in codex for tool in REVIEW_TOOLS)
    assert all(tool not in codex for tool in CORE_TOOLS | SOURCE_TOOLS | H3_TOOLS)
    assert claude["mcpServers"]["ask-ai-review"]["command"] == "__MCP_REVIEW_EXE__"
    for profile in ("core", "subagent", "h3", "full"):
        content = (MIGRATION / f"profiles/{profile}/config-templates/codex.toml").read_text("utf-8")
        assert all(tool not in content for tool in REVIEW_TOOLS)


def test_common_package_inputs_contain_no_personal_state_files() -> None:
    forbidden = {"usage.db", ".env", "config.toml", "claude_desktop_config.json"}
    names = {path.name for path in MIGRATION.rglob("*") if path.is_file()}
    assert names.isdisjoint(forbidden)
    security = (MIGRATION / "common/SECURITY_EXCLUSIONS_ZH-CN.md").read_text("utf-8")
    assert "Windows Credential Manager" in security
    assert "%LOCALAPPDATA%\\AskAIMCP" in security


def test_secret_pattern_does_not_confuse_project_name_with_api_key() -> None:
    pattern = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")
    assert pattern.search('ask-ai-mcp-credentials = "entrypoint"') is None
    assert pattern.search("token=sk-abcdefghijklmnopqrstuvwxyz123456") is not None

    assignment = re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*"
        r"""["'][A-Za-z0-9._-]{12,}["']"""
    )
    assert assignment.search("api_key = self.backend.get_password()") is None
    assert assignment.search('api_key = "abcdefghijklmnopqrstuvwxyz"') is not None


def test_installer_resolves_package_root_from_scripts_directory() -> None:
    installer = (MIGRATION / "common/scripts/install-mcp.ps1").read_text("utf-8")
    assert "$packageRoot = Split-Path -Parent $PSScriptRoot" in installer
    assert "Split-Path -Parent (Split-Path -Parent $PSScriptRoot)" not in installer


def test_builder_uses_sanitized_wheel_metadata_and_headerless_export() -> None:
    builder = (ROOT / "scripts/build_migration_packages.ps1").read_text("utf-8")
    assert "migration\\common\\PACKAGE_README.md" in builder
    assert "--no-header" in builder
    assert "Sensitive value detected inside wheel" in builder
    assert '$profiles = @("core", "subagent", "h3", "full")' in builder
    assert "full = 15" in builder


def test_installer_verifies_all_four_explicit_profiles() -> None:
    installer = (MIGRATION / "common/scripts/install-mcp.ps1").read_text("utf-8")
    assert '[ValidateSet("core", "subagent", "h3", "full")]' in installer
    assert "subagent = 11" in installer
    assert "h3 = 4" in installer
    assert "full = 15" in installer
