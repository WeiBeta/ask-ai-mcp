"""Defense-in-depth tests for untrusted candidate AST analysis."""

import pytest

from ask_ai_mcp.models import CandidateFile, ToolBuildSpec, ToolCandidatePayload, ToolCategory
from ask_ai_mcp.static_policy import analyze_candidate


def make_spec(*, allowed_packages: list[str] | None = None) -> ToolBuildSpec:
    return ToolBuildSpec(
        name="extract_tables",
        category=ToolCategory.TABLE_PROCESSOR,
        purpose="Extract synthetic table fixtures with stable source coordinates.",
        input_contract="Read-only fixture copies inside the isolated input directory.",
        output_contract="JSON-compatible table rows written to the output directory.",
        acceptance_tests=["The synthetic fixture produces deterministic rows."],
        allowed_packages=allowed_packages or [],
    )


def payload(source: str, *, path: str = "tool.py") -> ToolCandidatePayload:
    return ToolCandidatePayload(
        summary="A bounded test candidate.",
        files=[CandidateFile(path=path, content=source)],
    )


def test_safe_standard_library_candidate_is_allowed() -> None:
    report = analyze_candidate(
        make_spec(),
        payload("import json\n\ndef convert(value):\n    return json.loads(value)\n"),
    )
    assert report.allowed is True
    assert report.scanned_python_files == 1
    assert report.findings == []


def test_declared_document_package_import_is_allowed() -> None:
    report = analyze_candidate(
        make_spec(allowed_packages=["python-docx"]),
        payload("from docx import Document\n\ndef load(path):\n    return Document(path)\n"),
    )
    assert report.allowed is True


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("import os\n", "forbidden_import"),
        ("import requests\n", "forbidden_import"),
        ("import numpy\n", "unapproved_import"),
        ("value = eval('1 + 1')\n", "forbidden_call"),
        ("value = object.__subclasses__()\n", "forbidden_attribute"),
        ("path = 'C:/Users/user/source.docx'\n", "absolute_path_literal"),
    ],
)
def test_unsafe_constructs_are_rejected(source: str, code: str) -> None:
    report = analyze_candidate(make_spec(), payload(source))
    assert report.allowed is False
    assert code in {finding.code for finding in report.findings}


def test_syntax_error_is_rejected_without_echoing_source() -> None:
    report = analyze_candidate(make_spec(), payload("def broken(:\n    pass\n"))
    assert report.allowed is False
    assert report.findings[0].code == "syntax_error"
    assert "def broken" not in report.model_dump_json()


def test_local_candidate_module_import_is_allowed() -> None:
    candidate = ToolCandidatePayload(
        summary="Two local modules.",
        files=[
            CandidateFile(path="tool.py", content="import helper\n"),
            CandidateFile(path="helper.py", content="VALUE = 1\n"),
        ],
    )
    assert analyze_candidate(make_spec(), candidate).allowed is True
