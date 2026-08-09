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
    "open_budget_session",
    "budget_status",
    "add_budget_block",
    "close_budget_session",
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
    assert "__MCP_FULL_EXE__" in codex
    assert all(tool in codex for tool in CORE_TOOLS | H3_TOOLS)
    assert "ASK_AI_MCP_H3_URL" in codex
    package_inputs = [path.name.casefold() for path in MIGRATION.rglob("*") if path.is_file()]
    assert not any(name.endswith((".safetensors", ".pth", ".ckpt")) for name in package_inputs)


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
