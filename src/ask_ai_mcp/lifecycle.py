"""Bounded candidate generation, repair, execution, and review preparation."""

from __future__ import annotations

import difflib
from pathlib import PurePosixPath
from typing import Protocol

from ask_ai_mcp.deepseek import DeepSeekClient
from ask_ai_mcp.hashing import candidate_payload_sha256, tool_spec_sha256
from ask_ai_mcp.models import (
    CandidateAttemptReport,
    CandidateExecutionReport,
    CandidateJobState,
    CandidateLifecycleResult,
    CandidateLifecycleStatus,
    CandidateRepairFeedback,
    CandidateReviewBundle,
    StaticAnalysisReport,
    ToolBuildSpec,
    ToolCandidateResult,
)
from ask_ai_mcp.sandbox import DockerCandidateExecutor
from ask_ai_mcp.static_policy import analyze_candidate
from ask_ai_mcp.workspace import CandidateWorkspaceManager


class CandidateClient(Protocol):
    def build_candidate(
        self,
        spec: ToolBuildSpec,
        *,
        client_name: str,
        allow_pro: bool = False,
    ) -> ToolCandidateResult: ...

    def repair_candidate(
        self,
        spec: ToolBuildSpec,
        previous: ToolCandidateResult,
        feedback: CandidateRepairFeedback,
        *,
        client_name: str,
        allow_pro: bool = False,
    ) -> ToolCandidateResult: ...


class CandidateExecutor(Protocol):
    def execute(self, job_root) -> CandidateExecutionReport: ...


class CandidateLifecycleError(RuntimeError):
    """Raised when a candidate breaks the controller's trust contract."""


class CandidateLifecycle:
    """Allow one initial build and no more than two automatic repair rounds."""

    def __init__(
        self,
        *,
        client: CandidateClient | None = None,
        workspace: CandidateWorkspaceManager | None = None,
        executor: CandidateExecutor | None = None,
        max_repairs: int = 2,
    ) -> None:
        if not 0 <= max_repairs <= 2:
            raise ValueError("max_repairs must be between 0 and 2")
        self.client = client or DeepSeekClient()
        self.workspace = workspace or CandidateWorkspaceManager()
        self.executor = executor or DockerCandidateExecutor()
        self.max_repairs = max_repairs

    def run(
        self,
        spec: ToolBuildSpec,
        *,
        client_name: str,
        allow_pro: bool = False,
    ) -> CandidateLifecycleResult:
        spec_hash = tool_spec_sha256(spec)
        attempts: list[CandidateAttemptReport] = []
        candidate = self.client.build_candidate(spec, client_name=client_name, allow_pro=allow_pro)

        for attempt_number in range(1, self.max_repairs + 2):
            self._validate_candidate(spec, candidate)
            static_report = analyze_candidate(spec, candidate.payload)
            execution: CandidateExecutionReport | None = None
            job_id: str | None = None

            if static_report.allowed:
                job_root, manifest, _ = self.workspace.stage(spec=spec, result=candidate)
                job_id = manifest.job_id
                execution = self.executor.execute(job_root)
                state = execution.state
            else:
                state = CandidateJobState.STATIC_REJECTED

            attempt = CandidateAttemptReport(
                attempt=attempt_number,
                candidate_sha256=candidate.candidate_sha256,
                model=candidate.model,
                state=state,
                job_id=job_id,
                static_analysis=static_report,
                execution=execution,
            )
            attempts.append(attempt)

            if self._execution_passed(execution):
                assert job_id is not None
                review = self._review_bundle(
                    spec=spec,
                    spec_hash=spec_hash,
                    candidate=candidate,
                    job_id=job_id,
                    static_report=static_report,
                    execution=execution,
                    attempts=attempts,
                )
                return CandidateLifecycleResult(
                    status=CandidateLifecycleStatus.REVIEW_PENDING,
                    tool_name=spec.name,
                    spec_sha256=spec_hash,
                    attempts=attempts,
                    review=review,
                )

            if attempt_number > self.max_repairs:
                break
            feedback = self._repair_feedback(
                repair_round=attempt_number,
                static_report=static_report,
                execution=execution,
            )
            candidate = self.client.repair_candidate(
                spec,
                candidate,
                feedback,
                client_name=client_name,
                allow_pro=allow_pro,
            )

        return CandidateLifecycleResult(
            status=CandidateLifecycleStatus.FAILED,
            tool_name=spec.name,
            spec_sha256=spec_hash,
            attempts=attempts,
            failure_summary=(
                "Candidate did not pass policy and isolated tests within "
                f"{self.max_repairs} repair rounds."
            ),
        )

    @staticmethod
    def _validate_candidate(spec: ToolBuildSpec, candidate: ToolCandidateResult) -> None:
        if candidate.model is not spec.model:
            raise CandidateLifecycleError("candidate model does not match the specification")
        if not candidate.thinking_enabled:
            raise CandidateLifecycleError("tool candidate was not generated with thinking enabled")
        if candidate_payload_sha256(candidate.payload) != candidate.candidate_sha256:
            raise CandidateLifecycleError("candidate hash does not match its validated payload")

    @staticmethod
    def _execution_passed(execution: CandidateExecutionReport | None) -> bool:
        return bool(
            execution is not None
            and execution.state is CandidateJobState.EXECUTED
            and execution.exit_code == 0
            and execution.tests_run > 0
            and not execution.timed_out
        )

    @staticmethod
    def _repair_feedback(
        *,
        repair_round: int,
        static_report: StaticAnalysisReport,
        execution: CandidateExecutionReport | None,
    ) -> CandidateRepairFeedback:
        if not static_report.allowed:
            reason_codes = sorted({finding.code for finding in static_report.findings})
            excerpt = "\n".join(
                f"{finding.file_path}:{finding.line or 0} {finding.code}: {finding.message}"
                for finding in static_report.findings
            )
        else:
            assert execution is not None
            reason_codes = []
            if execution.timed_out:
                reason_codes.append("execution_timed_out")
            if execution.exit_code not in {None, 0}:
                reason_codes.append(f"execution_exit_{execution.exit_code}")
            if execution.tests_run == 0:
                reason_codes.append("no_tests_discovered")
            if execution.output_truncated:
                reason_codes.append("execution_output_truncated")
            if not reason_codes:
                reason_codes.append("isolated_tests_failed")
            excerpt = execution.stderr

        return CandidateRepairFeedback(
            repair_round=repair_round,
            reason_codes=reason_codes,
            diagnostic_excerpt=excerpt[-8_000:],
        )

    @staticmethod
    def _review_bundle(
        *,
        spec: ToolBuildSpec,
        spec_hash: str,
        candidate: ToolCandidateResult,
        job_id: str,
        static_report: StaticAnalysisReport,
        execution: CandidateExecutionReport,
        attempts: list[CandidateAttemptReport],
    ) -> CandidateReviewBundle:
        test_files = [
            file.path
            for file in candidate.payload.files
            if PurePosixPath(file.path).name.startswith("test")
            and file.path.casefold().endswith(".py")
        ]
        if not test_files:
            raise ValueError("successful candidate did not include reviewable test files")

        patch_parts: list[str] = []
        for file in candidate.payload.files:
            diff = difflib.unified_diff(
                [],
                file.content.splitlines(),
                fromfile="/dev/null",
                tofile=f"b/{file.path}",
                lineterm="",
            )
            patch_parts.append("\n".join(diff))

        return CandidateReviewBundle(
            tool_name=spec.name,
            spec_sha256=spec_hash,
            job_id=job_id,
            candidate_sha256=candidate.candidate_sha256,
            candidate_files=[file.path for file in candidate.payload.files],
            test_files=test_files,
            candidate_patch="\n\n".join(patch_parts),
            declared_risks=candidate.payload.risks,
            static_analysis=static_report,
            execution=execution,
            attempts=list(attempts),
        )
