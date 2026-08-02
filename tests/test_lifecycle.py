"""Tests for bounded candidate generation, repair, and review preparation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ask_ai_mcp.deepseek import InvalidCandidateResponse
from ask_ai_mcp.hashing import candidate_payload_sha256
from ask_ai_mcp.lifecycle import CandidateLifecycle, CandidateLifecycleError
from ask_ai_mcp.models import (
    CandidateExecutionReport,
    CandidateFile,
    CandidateJobManifest,
    CandidateJobState,
    CandidateLifecycleStatus,
    DeepSeekModel,
    RepairKind,
    ToolBuildSpec,
    ToolCandidatePayload,
    ToolCandidateResult,
    ToolCategory,
)
from ask_ai_mcp.review import CandidateReviewError, CandidateReviewRepository
from ask_ai_mcp.sandbox import DEFAULT_RUNNER_IMAGE
from ask_ai_mcp.usage import UsageStore
from ask_ai_mcp.workspace import CandidateWorkspaceManager

BUDGET_SESSION_ID = "11111111-1111-4111-8111-111111111111"


def make_spec() -> ToolBuildSpec:
    return ToolBuildSpec(
        name="normalize_fixture",
        category=ToolCategory.TEST_UTILITY,
        purpose="Normalize synthetic JSON fixtures for deterministic offline tests.",
        input_contract="An empty or synthetic-only isolated input directory.",
        output_contract="Normalized synthetic JSON in the isolated output directory.",
        acceptance_tests=["A stdlib unittest verifies deterministic normalization."],
    )


def make_candidate(*, unsafe: bool = False, include_test: bool = True) -> ToolCandidateResult:
    tool_source = (
        "import os\n"
        if unsafe
        else (
            "def normalize(value):\n    return value\n\n"
            "def run(request, input_dir, output_dir):\n"
            "    return normalize(request)\n"
        )
    )
    files = [CandidateFile(path="tool.py", content=tool_source)]
    if include_test:
        files.append(
            CandidateFile(
                path="test_tool.py",
                content=(
                    "import unittest\nfrom tool import normalize\n\n"
                    "class TestTool(unittest.TestCase):\n"
                    "    def test_value(self):\n        self.assertEqual(normalize(1), 1)\n"
                ),
            )
        )
    payload = ToolCandidatePayload(
        summary="A synthetic fixture normalizer.",
        files=files,
        risks=["Synthetic coverage is intentionally narrow."],
    )
    return ToolCandidateResult(
        candidate_sha256=candidate_payload_sha256(payload),
        model=DeepSeekModel.FLASH,
        thinking_enabled=True,
        payload=payload,
    )


class FakeClient:
    def __init__(self, candidates: list[ToolCandidateResult]) -> None:
        self.candidates = candidates
        self.repair_feedback = []
        self.repair_kwargs = []

    def build_candidate(self, *_args, **_kwargs) -> ToolCandidateResult:
        return self.candidates[0]

    def repair_candidate(self, _spec, _previous, feedback, **_kwargs) -> ToolCandidateResult:
        self.repair_feedback.append(feedback)
        self.repair_kwargs.append(_kwargs)
        return self.candidates[len(self.repair_feedback)]


class PassingExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, job_root: Path) -> CandidateExecutionReport:
        self.calls += 1
        manifest_path = job_root / "control" / "manifest.json"
        manifest = CandidateJobManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        report = CandidateExecutionReport(
            job_id=job_root.name,
            candidate_sha256=manifest.candidate_sha256,
            state=CandidateJobState.EXECUTED,
            backend="docker_desktop_wsl2",
            runner_image=DEFAULT_RUNNER_IMAGE,
            exit_code=0,
            tests_run=1,
            stderr="Ran 1 test in 0.001s\nOK\n",
        )
        (job_root / "control" / "execution.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )
        manifest_path.write_text(
            manifest.model_copy(
                update={
                    "state": CandidateJobState.EXECUTED,
                    "execution_backend": "docker_desktop_wsl2",
                }
            ).model_dump_json(indent=2),
            encoding="utf-8",
        )
        return report


class FailOnceExecutor(PassingExecutor):
    def execute(self, job_root: Path) -> CandidateExecutionReport:
        if self.calls:
            return super().execute(job_root)
        self.calls += 1
        manifest = CandidateJobManifest.model_validate_json(
            (job_root / "control" / "manifest.json").read_text(encoding="utf-8")
        )
        return CandidateExecutionReport(
            job_id=job_root.name,
            candidate_sha256=manifest.candidate_sha256,
            state=CandidateJobState.EXECUTION_FAILED,
            backend="docker_desktop_wsl2",
            runner_image=DEFAULT_RUNNER_IMAGE,
            exit_code=1,
            tests_run=1,
            stderr="synthetic assertion failed",
        )


class RegeneratingClient(FakeClient):
    def __init__(self, candidate: ToolCandidateResult) -> None:
        super().__init__([candidate])
        self.regenerations = 0

    def build_candidate(self, *_args, **_kwargs) -> ToolCandidateResult:
        raise InvalidCandidateResponse("invalid candidate")

    def regenerate_candidate(self, *_args, **_kwargs) -> ToolCandidateResult:
        self.regenerations += 1
        return self.candidates[0]


class FailingRepairClient(FakeClient):
    def repair_candidate(self, *_args, **_kwargs) -> ToolCandidateResult:
        raise RuntimeError("synthetic repair transport failure")


def test_static_failure_is_repaired_then_returned_for_review(tmp_path: Path) -> None:
    client = FakeClient([make_candidate(unsafe=True), make_candidate()])
    executor = PassingExecutor()
    lifecycle = CandidateLifecycle(
        client=client,
        workspace=CandidateWorkspaceManager(tmp_path / "jobs"),
        executor=executor,
    )

    result = lifecycle.run(make_spec(), client_name="codex", budget_session_id=BUDGET_SESSION_ID)

    assert result.status is CandidateLifecycleStatus.REVIEW_PENDING
    assert len(result.attempts) == 2
    assert result.attempts[0].state is CandidateJobState.STATIC_REJECTED
    assert result.attempts[1].state is CandidateJobState.EXECUTED
    assert "forbidden_import" in client.repair_feedback[0].reason_codes
    assert client.repair_kwargs[0]["thinking_enabled"] is False
    assert client.repair_kwargs[0]["max_output_tokens"] == 4096
    assert executor.calls == 1
    assert result.review_summary is not None
    assert result.review_summary.status is CandidateLifecycleStatus.REVIEW_PENDING
    assert result.review_summary.test_files == ["test_tool.py"]
    assert result.review_summary.declared_risks == ["Synthetic coverage is intentionally narrow."]
    assert "candidate_patch" not in result.model_dump_json()
    repository = CandidateReviewRepository(tmp_path / "jobs")
    full_review = repository.load(result.review_summary.job_id)
    assert "+++ b/tool.py" in full_review.candidate_patch
    assert repository.load_summary(result.review_summary.job_id) == result.review_summary


def test_persisted_review_detects_candidate_tampering(tmp_path: Path) -> None:
    lifecycle = CandidateLifecycle(
        client=FakeClient([make_candidate()]),
        workspace=CandidateWorkspaceManager(tmp_path / "jobs"),
        executor=PassingExecutor(),
    )
    result = lifecycle.run(make_spec(), client_name="codex", budget_session_id=BUDGET_SESSION_ID)
    assert result.review_summary is not None
    job_root = tmp_path / "jobs" / result.review_summary.job_id
    (job_root / "candidate" / "tool.py").write_text("changed = True\n", encoding="utf-8")

    with pytest.raises(CandidateReviewError, match="content changed"):
        CandidateReviewRepository(tmp_path / "jobs").load(result.review_summary.job_id)


def test_review_remains_revalidatable_for_second_desktop_approval(tmp_path: Path) -> None:
    repository = CandidateReviewRepository(tmp_path / "jobs")
    lifecycle = CandidateLifecycle(
        client=FakeClient([make_candidate()]),
        workspace=CandidateWorkspaceManager(tmp_path / "jobs"),
        executor=PassingExecutor(),
        review_repository=repository,
    )
    result = lifecycle.run(make_spec(), client_name="codex", budget_session_id=BUDGET_SESSION_ID)
    assert result.review_summary is not None
    manifest_path = tmp_path / "jobs" / result.review_summary.job_id / "control" / "manifest.json"
    manifest = CandidateJobManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(
        manifest.model_copy(update={"state": CandidateJobState.APPROVED}).model_dump_json(indent=2),
        encoding="utf-8",
    )

    assert repository.load_summary(result.review_summary.job_id) == result.review_summary


def test_static_policy_failure_gets_only_one_repair(tmp_path: Path) -> None:
    client = FakeClient([make_candidate(unsafe=True) for _ in range(2)])
    lifecycle = CandidateLifecycle(
        client=client,
        workspace=CandidateWorkspaceManager(tmp_path / "jobs"),
        executor=PassingExecutor(),
    )

    result = lifecycle.run(make_spec(), client_name="claude", budget_session_id=BUDGET_SESSION_ID)

    assert result.status is CandidateLifecycleStatus.FAILED
    assert len(result.attempts) == 2
    assert len(client.repair_feedback) == 1
    assert result.review_summary is None


def test_semantic_failure_uses_thinking_high_repair(tmp_path: Path) -> None:
    client = FakeClient([make_candidate(), make_candidate()])
    lifecycle = CandidateLifecycle(
        client=client,
        workspace=CandidateWorkspaceManager(tmp_path / "jobs"),
        executor=FailOnceExecutor(),
    )

    result = lifecycle.run(make_spec(), client_name="codex", budget_session_id=BUDGET_SESSION_ID)

    assert result.status is CandidateLifecycleStatus.REVIEW_PENDING
    assert client.repair_feedback[0].repair_kind is RepairKind.SEMANTIC_TEST
    assert client.repair_kwargs[0]["thinking_enabled"] is True
    assert client.repair_kwargs[0]["max_output_tokens"] == 16_384
    assert result.attempts[1].repair_kind is RepairKind.SEMANTIC_TEST


def test_invalid_initial_response_is_regenerated_once(tmp_path: Path) -> None:
    client = RegeneratingClient(make_candidate())
    lifecycle = CandidateLifecycle(
        client=client,
        workspace=CandidateWorkspaceManager(tmp_path / "jobs"),
        executor=PassingExecutor(),
    )

    result = lifecycle.run(make_spec(), client_name="codex", budget_session_id=BUDGET_SESSION_ID)

    assert result.status is CandidateLifecycleStatus.REVIEW_PENDING
    assert client.regenerations == 1


def test_lifecycle_records_structural_metrics_without_content(tmp_path: Path) -> None:
    database = tmp_path / "usage.db"
    lifecycle = CandidateLifecycle(
        client=FakeClient([make_candidate()]),
        workspace=CandidateWorkspaceManager(tmp_path / "jobs"),
        executor=PassingExecutor(),
        audit_store=UsageStore(database),
    )

    result = lifecycle.run(
        make_spec(), client_name="codex_desktop", budget_session_id=BUDGET_SESSION_ID
    )

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT lifecycle_id, budget_session_id, client_name, status,
                   spec_total_chars, candidate_source_chars, candidate_test_chars,
                   review_summary_chars, patch_chars, attempt_count, repair_count
            FROM lifecycle_audit
            """
        ).fetchone()
    assert row[:4] == (
        result.lifecycle_id,
        BUDGET_SESSION_ID,
        "codex_desktop",
        CandidateLifecycleStatus.REVIEW_PENDING.value,
    )
    assert all(value > 0 for value in row[4:9])
    assert row[9:] == (1, 0)


def test_failed_repair_still_records_lifecycle_audit(tmp_path: Path) -> None:
    database = tmp_path / "usage.db"
    lifecycle = CandidateLifecycle(
        client=FailingRepairClient([make_candidate(unsafe=True)]),
        workspace=CandidateWorkspaceManager(tmp_path / "jobs"),
        executor=PassingExecutor(),
        audit_store=UsageStore(database),
    )

    with pytest.raises(RuntimeError, match="synthetic repair transport failure"):
        lifecycle.run(make_spec(), client_name="codex_desktop", budget_session_id=BUDGET_SESSION_ID)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status, attempt_count, repair_count FROM lifecycle_audit"
        ).fetchone()
    assert row == (CandidateLifecycleStatus.FAILED.value, 1, 0)


def test_missing_tests_are_rejected_before_docker_and_repaired(tmp_path: Path) -> None:
    client = FakeClient([make_candidate(include_test=False), make_candidate()])
    executor = PassingExecutor()
    lifecycle = CandidateLifecycle(
        client=client,
        workspace=CandidateWorkspaceManager(tmp_path / "jobs"),
        executor=executor,
    )

    result = lifecycle.run(make_spec(), client_name="codex", budget_session_id=BUDGET_SESSION_ID)

    assert result.status is CandidateLifecycleStatus.REVIEW_PENDING
    assert len(result.attempts) == 2
    assert result.attempts[0].state is CandidateJobState.STATIC_REJECTED
    assert "missing_test_file" in client.repair_feedback[0].reason_codes
    assert executor.calls == 1


def test_client_hash_mismatch_is_rejected_before_staging(tmp_path: Path) -> None:
    mismatched = make_candidate().model_copy(update={"candidate_sha256": "0" * 64})
    lifecycle = CandidateLifecycle(
        client=FakeClient([mismatched]),
        workspace=CandidateWorkspaceManager(tmp_path / "jobs"),
        executor=PassingExecutor(),
    )

    with pytest.raises(CandidateLifecycleError, match="hash"):
        lifecycle.run(make_spec(), client_name="codex", budget_session_id=BUDGET_SESSION_ID)
    assert not any((tmp_path / "jobs").iterdir())
