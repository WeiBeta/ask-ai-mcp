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


TEST_SOURCE = (
    "import unittest\n\n"
    "class TestTool(unittest.TestCase):\n"
    "    def test_placeholder(self):\n"
    "        self.assertTrue(True)\n"
)


def payload(
    source: str, *, path: str = "tool.py", include_test: bool = True
) -> ToolCandidatePayload:
    files = [CandidateFile(path=path, content=source)]
    if include_test:
        files.append(CandidateFile(path="test_tool.py", content=TEST_SOURCE))
    return ToolCandidatePayload(
        summary="A bounded test candidate.",
        files=files,
    )


def test_safe_standard_library_candidate_is_allowed() -> None:
    report = analyze_candidate(
        make_spec(),
        payload(
            "import json\n\ndef convert(value):\n    return json.loads(value)\n\n"
            "def run(request, input_dir, output_dir):\n    return convert(request)\n"
        ),
    )
    assert report.allowed is True
    assert report.scanned_python_files == 2
    assert report.findings == []


def test_declared_document_package_import_is_allowed() -> None:
    report = analyze_candidate(
        make_spec(allowed_packages=["python-docx"]),
        payload(
            "from docx import Document\n\ndef load(path):\n    return Document(path)\n\n"
            "def run(request, input_dir, output_dir):\n    return {'ok': True}\n"
        ),
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
            CandidateFile(
                path="tool.py",
                content=(
                    "import helper\n\n"
                    "def run(request, input_dir, output_dir):\n    return helper.VALUE\n"
                ),
            ),
            CandidateFile(path="helper.py", content="VALUE = 1\n"),
            CandidateFile(path="test_tool.py", content=TEST_SOURCE),
        ],
    )
    assert analyze_candidate(make_spec(), candidate).allowed is True


def test_entrypoint_requires_exact_run_signature() -> None:
    report = analyze_candidate(
        make_spec(),
        payload("def run(value):\n    return value\n"),
    )
    assert report.allowed is False
    assert "invalid_run_signature" in {finding.code for finding in report.findings}


def test_candidate_requires_discoverable_unittest_file() -> None:
    report = analyze_candidate(
        make_spec(),
        payload(
            "def run(request, input_dir, output_dir):\n    return request\n",
            include_test=False,
        ),
    )

    assert report.allowed is False
    assert "missing_test_file" in {finding.code for finding in report.findings}


def test_plain_test_function_is_not_treated_as_unittest_case() -> None:
    candidate = ToolCandidatePayload(
        summary="A candidate with an undiscoverable test.",
        files=[
            CandidateFile(
                path="tool.py",
                content="def run(request, input_dir, output_dir):\n    return request\n",
            ),
            CandidateFile(path="test_tool.py", content="def test_value():\n    assert True\n"),
        ],
    )

    report = analyze_candidate(make_spec(), candidate)
    assert report.allowed is False
    assert "missing_unittest_case" in {finding.code for finding in report.findings}


def test_tempfile_is_allowed_only_in_test_files() -> None:
    test_source = (
        "import tempfile\nimport unittest\n\n"
        "class TestTool(unittest.TestCase):\n"
        "    def test_temp_directory(self):\n"
        "        with tempfile.TemporaryDirectory() as directory:\n"
        "            self.assertTrue(directory)\n"
    )
    candidate = ToolCandidatePayload(
        summary="A candidate using a test-only temporary directory.",
        files=[
            CandidateFile(
                path="tool.py",
                content="def run(request, input_dir, output_dir):\n    return request\n",
            ),
            CandidateFile(path="test_tool.py", content=test_source),
        ],
    )
    assert analyze_candidate(make_spec(), candidate).allowed is True

    report = analyze_candidate(
        make_spec(),
        payload(
            "import tempfile\n\n"
            "def run(request, input_dir, output_dir):\n    return request\n"
        ),
    )
    assert report.allowed is False
    assert "forbidden_import" in {finding.code for finding in report.findings}
