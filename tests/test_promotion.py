"""Tests for explicit, hash-pinned candidate promotion."""

from __future__ import annotations

from pathlib import Path

import pytest

from ask_ai_mcp.hashing import candidate_payload_sha256
from ask_ai_mcp.models import (
    CandidateApprovalRequest,
    CandidateDecision,
    CandidateExecutionReport,
    CandidateFile,
    CandidateJobManifest,
    CandidateJobState,
    DeepSeekModel,
    ToolBuildSpec,
    ToolCandidatePayload,
    ToolCandidateResult,
    ToolCategory,
)
from ask_ai_mcp.promotion import CandidatePromotionError, VerifiedToolRegistry
from ask_ai_mcp.sandbox import DEFAULT_RUNNER_IMAGE
from ask_ai_mcp.workspace import CandidateWorkspaceManager


def make_executed_job(tmp_path: Path):
    spec = ToolBuildSpec(
        name="fixture_counter",
        category=ToolCategory.TEST_UTILITY,
        purpose="Count records in synthetic fixtures for deterministic tests.",
        input_contract="Synthetic JSON records in the isolated input directory.",
        output_contract="A synthetic record count in the isolated output directory.",
        acceptance_tests=["A unittest verifies the expected synthetic count."],
    )
    payload = ToolCandidatePayload(
        summary="A bounded synthetic fixture counter.",
        files=[
            CandidateFile(
                path="tool.py",
                content=(
                    "def count(items):\n    return len(items)\n\n"
                    "def run(request, input_dir, output_dir):\n"
                    "    return {'count': count(request.get('items', []))}\n"
                ),
            ),
            CandidateFile(
                path="test_tool.py",
                content=(
                    "import unittest\nfrom tool import count\n\n"
                    "class TestCount(unittest.TestCase):\n"
                    "    def test_count(self):\n        self.assertEqual(count([1, 2]), 2)\n"
                ),
            ),
        ],
    )
    result = ToolCandidateResult(
        candidate_sha256=candidate_payload_sha256(payload),
        model=DeepSeekModel.FLASH,
        thinking_enabled=True,
        payload=payload,
    )
    job_root, manifest, _ = CandidateWorkspaceManager(tmp_path / "jobs").stage(
        spec=spec, result=result
    )
    execution = CandidateExecutionReport(
        job_id=manifest.job_id,
        candidate_sha256=manifest.candidate_sha256,
        state=CandidateJobState.EXECUTED,
        backend="docker_desktop_wsl2",
        runner_image=DEFAULT_RUNNER_IMAGE,
        exit_code=0,
        tests_run=1,
    )
    (job_root / "control" / "execution.json").write_text(
        execution.model_dump_json(indent=2), encoding="utf-8"
    )
    executed_manifest = manifest.model_copy(
        update={
            "state": CandidateJobState.EXECUTED,
            "execution_backend": "docker_desktop_wsl2",
        }
    )
    (job_root / "control" / "manifest.json").write_text(
        executed_manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return job_root, executed_manifest


def approval(manifest: CandidateJobManifest, **updates) -> CandidateApprovalRequest:
    values = {
        "job_id": manifest.job_id,
        "candidate_sha256": manifest.candidate_sha256,
        "version": "0.1.0",
        "approved_by": "codex_sol",
        "decision": CandidateDecision.APPROVED,
        "allowed_capabilities": ["read_synthetic_inputs", "write_dedicated_output"],
    }
    values.update(updates)
    return CandidateApprovalRequest(**values)


def test_exact_successful_candidate_can_be_registered_and_reverified(tmp_path: Path) -> None:
    job_root, manifest = make_executed_job(tmp_path)
    registry = VerifiedToolRegistry(tmp_path / "verified", jobs_root=tmp_path / "jobs")

    target, record = registry.approve(job_root=job_root, request=approval(manifest))

    assert record.sha256 == manifest.candidate_sha256
    assert record.tests_run == 1
    assert (target / "candidate" / "tool.py").is_file()
    _, loaded = registry.load(
        name=record.name, version=record.version, candidate_sha256=record.sha256
    )
    assert loaded == record
    source_manifest = CandidateJobManifest.model_validate_json(
        (job_root / "control" / "manifest.json").read_text(encoding="utf-8")
    )
    assert source_manifest.state is CandidateJobState.APPROVED
    assert (job_root / ".retention.json").is_file()


def test_hash_mismatch_blocks_promotion(tmp_path: Path) -> None:
    job_root, manifest = make_executed_job(tmp_path)
    registry = VerifiedToolRegistry(tmp_path / "verified", jobs_root=tmp_path / "jobs")
    request = approval(manifest, candidate_sha256="0" * 64)

    with pytest.raises(CandidatePromotionError, match="hash mismatch"):
        registry.approve(job_root=job_root, request=request)


def test_job_outside_configured_jobs_root_is_rejected(tmp_path: Path) -> None:
    job_root, manifest = make_executed_job(tmp_path)
    registry = VerifiedToolRegistry(
        tmp_path / "verified", jobs_root=tmp_path / "different-jobs-root"
    )

    with pytest.raises(CandidatePromotionError, match="outside"):
        registry.approve(job_root=job_root, request=approval(manifest))


def test_execution_report_must_bind_the_same_candidate_hash(tmp_path: Path) -> None:
    job_root, manifest = make_executed_job(tmp_path)
    execution_path = job_root / "control" / "execution.json"
    execution = CandidateExecutionReport.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    ).model_copy(update={"candidate_sha256": "0" * 64})
    execution_path.write_text(execution.model_dump_json(indent=2), encoding="utf-8")
    registry = VerifiedToolRegistry(tmp_path / "verified", jobs_root=tmp_path / "jobs")

    with pytest.raises(CandidatePromotionError, match="does not qualify"):
        registry.approve(job_root=job_root, request=approval(manifest))


def test_approved_with_changes_requires_new_tested_hash(tmp_path: Path) -> None:
    job_root, manifest = make_executed_job(tmp_path)
    registry = VerifiedToolRegistry(tmp_path / "verified", jobs_root=tmp_path / "jobs")
    request = approval(manifest, decision=CandidateDecision.APPROVED_WITH_CHANGES)

    with pytest.raises(CandidatePromotionError, match="fresh test run"):
        registry.approve(job_root=job_root, request=request)


def test_registered_file_change_invalidates_record(tmp_path: Path) -> None:
    job_root, manifest = make_executed_job(tmp_path)
    registry = VerifiedToolRegistry(tmp_path / "verified", jobs_root=tmp_path / "jobs")
    target, record = registry.approve(job_root=job_root, request=approval(manifest))
    (target / "candidate" / "tool.py").write_text("changed = True\n", encoding="utf-8")

    with pytest.raises(CandidatePromotionError, match="no longer match"):
        registry.load(name=record.name, version=record.version, candidate_sha256=record.sha256)


def test_second_desktop_approval_is_appended_without_changing_candidate(
    tmp_path: Path,
) -> None:
    job_root, manifest = make_executed_job(tmp_path)
    registry = VerifiedToolRegistry(tmp_path / "verified", jobs_root=tmp_path / "jobs")
    _, first = registry.approve(
        job_root=job_root,
        request=approval(manifest, approved_by="codex_desktop"),
    )
    _, second = registry.approve(
        job_root=job_root,
        request=approval(manifest, approved_by="claude_desktop"),
    )

    assert first.approval_identities == ["codex_desktop"]
    assert second.approval_identities == ["codex_desktop", "claude_desktop"]
    assert second.file_sha256 == first.file_sha256
    assert registry.list_records() == [second]
