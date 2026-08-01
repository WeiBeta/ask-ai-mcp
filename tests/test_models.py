"""Tests for strict domain contracts."""

import pytest
from pydantic import ValidationError

from ask_ai_mcp.models import CandidateFile, ToolBuildSpec, ToolCandidatePayload, ToolCategory


def test_tool_spec_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ToolBuildSpec(
            name="extract_tables",
            category=ToolCategory.TABLE_PROCESSOR,
            purpose="Extract tables without changing source documents.",
            input_contract="Read-only copied XLSX files.",
            output_contract="JSON rows with source cell coordinates.",
            acceptance_tests=["The source workbook hash remains unchanged."],
            unexpected="must be rejected",
        )


@pytest.mark.parametrize("name", ["A", "has-dash", "2bad", "contains space"])
def test_tool_spec_requires_stable_tool_name(name: str) -> None:
    with pytest.raises(ValidationError):
        ToolBuildSpec(
            name=name,
            category=ToolCategory.TEST_UTILITY,
            purpose="Create deterministic fixture metadata for offline tests.",
            input_contract="A fixture directory inside the isolated job.",
            output_contract="A JSON manifest containing hashes and sizes.",
            acceptance_tests=["Every fixture appears exactly once."],
        )


@pytest.mark.parametrize("path", ["../escape.py", "C:/escape.py", "nested\\escape.py", "run.exe"])
def test_candidate_file_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        CandidateFile(path=path, content="pass")


def test_candidate_payload_rejects_duplicate_paths_case_insensitively() -> None:
    with pytest.raises(ValidationError, match="unique"):
        ToolCandidatePayload(
            summary="Duplicate output should not be accepted.",
            files=[
                CandidateFile(path="tool.py", content="pass"),
                CandidateFile(path="TOOL.py", content="pass"),
            ],
        )
