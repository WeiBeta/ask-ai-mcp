"""Tests for non-executing candidate staging outside the repository."""

import hashlib
from pathlib import Path

import pytest

from ask_ai_mcp.hashing import candidate_payload_sha256
from ask_ai_mcp.models import (
    CandidateFile,
    CandidateJobState,
    DeepSeekModel,
    ToolBuildSpec,
    ToolCandidatePayload,
    ToolCandidateResult,
    ToolCategory,
)
from ask_ai_mcp.workspace import CandidateWorkspaceError, CandidateWorkspaceManager


def make_items() -> tuple[ToolBuildSpec, ToolCandidateResult]:
    spec = ToolBuildSpec(
        name="normalize_fixture",
        category=ToolCategory.TEST_UTILITY,
        purpose="Normalize a synthetic fixture without touching original documents.",
        input_contract="A copied synthetic JSON fixture in the job input directory.",
        output_contract="Normalized JSON written only to the job output directory.",
        acceptance_tests=["The output contains stable sorted keys."],
    )
    payload = ToolCandidatePayload(
        summary="A deterministic fixture normalizer.",
        files=[
            CandidateFile(
                path="tool.py",
                content=(
                    "import json\n\ndef normalize(value):\n    return json.loads(value)\n\n"
                    "def run(request, input_dir, output_dir):\n"
                    "    return normalize(json.dumps(request))\n"
                ),
            ),
            CandidateFile(path="tests/test_tool.py", content="def test_true():\n    assert True\n"),
        ],
    )
    digest = candidate_payload_sha256(payload)
    result = ToolCandidateResult(
        candidate_sha256=digest,
        model=DeepSeekModel.FLASH,
        thinking_enabled=True,
        payload=payload,
    )
    return spec, result


def test_stage_writes_only_candidate_and_content_free_control_files(tmp_path: Path) -> None:
    spec, result = make_items()
    job_root, manifest, report = CandidateWorkspaceManager(tmp_path / "jobs").stage(
        spec=spec, result=result
    )

    assert job_root.is_relative_to((tmp_path / "jobs").resolve())
    assert manifest.state is CandidateJobState.STATIC_APPROVED
    assert report.allowed is True
    assert (job_root / "candidate" / "tool.py").read_bytes() == result.payload.files[
        0
    ].content.encode("utf-8")
    assert (job_root / "input").is_dir()
    assert (job_root / "output").is_dir()
    assert (job_root / "control" / "spec.json").is_file()
    manifest_text = (job_root / "control" / "manifest.json").read_text(encoding="utf-8")
    assert result.payload.files[0].content not in manifest_text
    assert (
        manifest.candidate_file_sha256["tool.py"]
        == hashlib.sha256(result.payload.files[0].content.encode("utf-8")).hexdigest()
    )


def test_stage_rejects_hash_mismatch(tmp_path: Path) -> None:
    spec, result = make_items()
    mismatched = result.model_copy(update={"candidate_sha256": "0" * 64})
    with pytest.raises(CandidateWorkspaceError, match="hash"):
        CandidateWorkspaceManager(tmp_path / "jobs").stage(spec=spec, result=mismatched)


def test_stage_rejects_static_policy_failure(tmp_path: Path) -> None:
    spec, result = make_items()
    unsafe_payload = result.payload.model_copy(
        update={
            "files": [CandidateFile(path="tool.py", content="import os\n")],
        }
    )
    unsafe_result = result.model_copy(
        update={
            "payload": unsafe_payload,
            "candidate_sha256": candidate_payload_sha256(unsafe_payload),
        }
    )
    with pytest.raises(CandidateWorkspaceError, match="static policy"):
        CandidateWorkspaceManager(tmp_path / "jobs").stage(spec=spec, result=unsafe_result)
