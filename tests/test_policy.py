"""Tests that protect the non-overridable model responsibility boundary."""

import pytest

from ask_ai_mcp.models import RuntimeKind, ToolBuildSpec, ToolCategory
from ask_ai_mcp.policy import evaluate_tool_spec


def make_spec(**overrides: object) -> ToolBuildSpec:
    values: dict[str, object] = {
        "name": "extract_xlsx_tables",
        "category": ToolCategory.TABLE_PROCESSOR,
        "purpose": "Extract worksheet tables while preserving source coordinates.",
        "input_contract": "Read-only copies of XLSX files in the job input directory.",
        "output_contract": "JSON tables with file, sheet, row, and cell provenance.",
        "acceptance_tests": [
            "Original fixture hashes remain unchanged.",
            "Every output cell contains a source coordinate.",
        ],
        "allowed_packages": ["openpyxl"],
    }
    values.update(overrides)
    return ToolBuildSpec.model_validate(values)


def test_bounded_preprocessor_is_allowed() -> None:
    decision = evaluate_tool_spec(make_spec())
    assert decision.allowed is True
    assert decision.reasons == []


@pytest.mark.parametrize(
    "purpose",
    [
        "撰写项目技术架构报告正文并形成最终成稿。",
        "根据资料共同撰写第五章节的交付正文。",
        "Write the final report chapter from the supplied evidence.",
        "Co-author an executive summary for direct delivery.",
    ],
)
def test_final_document_prose_is_rejected(purpose: str) -> None:
    decision = evaluate_tool_spec(make_spec(purpose=purpose))
    assert decision.allowed is False
    assert "final_document_prose_is_reserved_for_opus_or_sol" in decision.reasons


def test_powershell_is_rejected_until_high_risk_runner_exists() -> None:
    decision = evaluate_tool_spec(make_spec(runtime=RuntimeKind.POWERSHELL))
    assert decision.allowed is False
    assert "powershell_candidates_are_not_enabled_in_phase_0" in decision.reasons


def test_unapproved_package_is_rejected() -> None:
    decision = evaluate_tool_spec(make_spec(allowed_packages=["requests"]))
    assert decision.allowed is False
    assert "packages_not_allow_listed:requests" in decision.reasons


def test_final_prose_request_hidden_in_fixture_notes_is_rejected() -> None:
    decision = evaluate_tool_spec(
        make_spec(fixture_notes="另外生成项目报告正文, 内容将直接用于最终交付。")
    )
    assert decision.allowed is False
    assert "final_document_prose_is_reserved_for_opus_or_sol" in decision.reasons
