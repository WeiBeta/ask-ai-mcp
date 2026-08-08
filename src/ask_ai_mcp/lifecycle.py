"""Bounded candidate generation, repair, execution, and review preparation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Protocol
from uuid import uuid4

from ask_ai_mcp.deepseek import (
    SEMANTIC_REPAIR_MAX_OUTPUT_TOKENS,
    STATIC_REPAIR_MAX_OUTPUT_TOKENS,
    DeepSeekClient,
    InvalidCandidateResponse,
)
from ask_ai_mcp.hashing import candidate_payload_sha256, tool_spec_sha256
from ask_ai_mcp.models import (
    CandidateAttemptReport,
    CandidateExecutionReport,
    CandidateJobState,
    CandidateLifecycleResult,
    CandidateLifecycleStatus,
    CandidateRepairFeedback,
    CandidateReviewBundle,
    LifecycleAuditEvent,
    RepairKind,
    StaticAnalysisReport,
    ToolBuildSpec,
    ToolCandidateResult,
)
from ask_ai_mcp.review import (
    CandidateReviewRepository,
    attempt_summary,
    candidate_patch,
    review_summary,
)
from ask_ai_mcp.sandbox import DockerCandidateExecutor
from ask_ai_mcp.static_policy import analyze_candidate
from ask_ai_mcp.usage import UsageStore
from ask_ai_mcp.workspace import CandidateWorkspaceManager

MAX_TOTAL_ATTEMPTS = 3
MAX_STATIC_REPAIRS = 1
MAX_SEMANTIC_REPAIRS = 2


class CandidateClient(Protocol):
    def build_candidate(
        self,
        spec: ToolBuildSpec,
        *,
        client_name: str,
        allow_pro: bool = False,
        budget_session_id: str | None = None,
        lifecycle_id: str | None = None,
    ) -> ToolCandidateResult: ...

    def regenerate_candidate(
        self,
        spec: ToolBuildSpec,
        *,
        client_name: str,
        allow_pro: bool = False,
        budget_session_id: str | None = None,
        lifecycle_id: str | None = None,
    ) -> ToolCandidateResult: ...

    def repair_candidate(
        self,
        spec: ToolBuildSpec,
        previous: ToolCandidateResult,
        feedback: CandidateRepairFeedback,
        *,
        client_name: str,
        allow_pro: bool = False,
        thinking_enabled: bool = True,
        max_output_tokens: int = SEMANTIC_REPAIR_MAX_OUTPUT_TOKENS,
        budget_session_id: str | None = None,
        lifecycle_id: str | None = None,
    ) -> ToolCandidateResult: ...


class CandidateExecutor(Protocol):
    def execute(self, job_root) -> CandidateExecutionReport: ...


class CandidateLifecycleError(RuntimeError):
    """Raised when a candidate breaks the controller's trust contract."""


class CandidateLifecycle:
    """Route at most three attempts through fixed policy-dependent repair modes."""

    def __init__(
        self,
        *,
        client: CandidateClient | None = None,
        workspace: CandidateWorkspaceManager | None = None,
        executor: CandidateExecutor | None = None,
        review_repository: CandidateReviewRepository | None = None,
        audit_store: UsageStore | None = None,
    ) -> None:
        self.client = client if client is not None else DeepSeekClient()
        self.workspace = workspace if workspace is not None else CandidateWorkspaceManager()
        self.executor = executor if executor is not None else DockerCandidateExecutor()
        self.review_repository = review_repository or CandidateReviewRepository(
            self.workspace.jobs_root
        )
        self.audit_store = audit_store

    def run(
        self,
        spec: ToolBuildSpec,
        *,
        client_name: str,
        budget_session_id: str,
        allow_pro: bool = False,
    ) -> CandidateLifecycleResult:
        lifecycle_id = str(uuid4())
        started_at = datetime.now(UTC)
        spec_hash = tool_spec_sha256(spec)
        attempts: list[CandidateAttemptReport] = []
        static_repairs = 0
        semantic_repairs = 0
        candidate: ToolCandidateResult | None = None
        review: CandidateReviewBundle | None = None

        try:
            candidate = self._initial_candidate(
                spec,
                client_name=client_name,
                allow_pro=allow_pro,
                budget_session_id=budget_session_id,
                lifecycle_id=lifecycle_id,
            )
            generated_by: RepairKind | None = None

            for attempt_number in range(1, MAX_TOTAL_ATTEMPTS + 1):
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
                    thinking_enabled=candidate.thinking_enabled,
                    repair_kind=generated_by,
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
                    self.review_repository.save(review)
                    summary = review_summary(review, spec).model_copy(
                        update={
                            "created_by": client_name,
                            "blocking_reasons": ["candidate_not_registered"],
                            "next_action": (
                                "Review the exact candidate as needed, then approve its exact "
                                "hash and least-privilege capabilities."
                            ),
                        }
                    )
                    result = CandidateLifecycleResult(
                        lifecycle_id=lifecycle_id,
                        budget_session_id=budget_session_id,
                        status=CandidateLifecycleStatus.REVIEW_PENDING,
                        tool_name=spec.name,
                        spec_sha256=spec_hash,
                        attempts=[attempt_summary(item) for item in attempts],
                        review_summary=summary,
                    )
                    self._record_audit(
                        spec=spec,
                        spec_hash=spec_hash,
                        lifecycle_id=lifecycle_id,
                        budget_session_id=budget_session_id,
                        client_name=client_name,
                        started_at=started_at,
                        attempts=attempts,
                        candidate=candidate,
                        review=review,
                        result=result,
                    )
                    return result

                if attempt_number >= MAX_TOTAL_ATTEMPTS:
                    break

                if static_report.allowed:
                    if semantic_repairs >= MAX_SEMANTIC_REPAIRS:
                        break
                    repair_kind = RepairKind.SEMANTIC_TEST
                    semantic_repairs += 1
                    thinking_enabled = True
                    max_output_tokens = SEMANTIC_REPAIR_MAX_OUTPUT_TOKENS
                else:
                    if static_repairs >= MAX_STATIC_REPAIRS:
                        break
                    repair_kind = RepairKind.STATIC_POLICY
                    static_repairs += 1
                    thinking_enabled = False
                    max_output_tokens = STATIC_REPAIR_MAX_OUTPUT_TOKENS

                feedback = self._repair_feedback(
                    repair_round=attempt_number,
                    repair_kind=repair_kind,
                    static_report=static_report,
                    execution=execution,
                )
                candidate = self.client.repair_candidate(
                    spec,
                    candidate,
                    feedback,
                    client_name=client_name,
                    allow_pro=allow_pro,
                    thinking_enabled=thinking_enabled,
                    max_output_tokens=max_output_tokens,
                    budget_session_id=budget_session_id,
                    lifecycle_id=lifecycle_id,
                )
                generated_by = repair_kind

            result = CandidateLifecycleResult(
                lifecycle_id=lifecycle_id,
                budget_session_id=budget_session_id,
                status=CandidateLifecycleStatus.FAILED,
                tool_name=spec.name,
                spec_sha256=spec_hash,
                attempts=[attempt_summary(item) for item in attempts],
                failure_summary="Candidate did not pass within the bounded repair policy.",
            )
            self._record_audit(
                spec=spec,
                spec_hash=spec_hash,
                lifecycle_id=lifecycle_id,
                budget_session_id=budget_session_id,
                client_name=client_name,
                started_at=started_at,
                attempts=attempts,
                candidate=candidate,
                review=None,
                result=result,
            )
            return result
        except Exception:
            if self.audit_store is not None:
                self._record_audit(
                    spec=spec,
                    spec_hash=spec_hash,
                    lifecycle_id=lifecycle_id,
                    budget_session_id=budget_session_id,
                    client_name=client_name,
                    started_at=started_at,
                    attempts=attempts,
                    candidate=candidate,
                    review=None,
                    result=None,
                )
            raise

    def _initial_candidate(
        self,
        spec: ToolBuildSpec,
        *,
        client_name: str,
        allow_pro: bool,
        budget_session_id: str,
        lifecycle_id: str,
    ) -> ToolCandidateResult:
        try:
            return self.client.build_candidate(
                spec,
                client_name=client_name,
                allow_pro=allow_pro,
                budget_session_id=budget_session_id,
                lifecycle_id=lifecycle_id,
            )
        except InvalidCandidateResponse:
            return self.client.regenerate_candidate(
                spec,
                client_name=client_name,
                allow_pro=allow_pro,
                budget_session_id=budget_session_id,
                lifecycle_id=lifecycle_id,
            )

    @staticmethod
    def _validate_candidate(spec: ToolBuildSpec, candidate: ToolCandidateResult) -> None:
        if candidate.model is not spec.model:
            raise CandidateLifecycleError("candidate model does not match the specification")
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
        repair_kind: RepairKind,
        static_report: StaticAnalysisReport,
        execution: CandidateExecutionReport | None,
    ) -> CandidateRepairFeedback:
        if repair_kind is RepairKind.STATIC_POLICY:
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
            repair_kind=repair_kind,
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
            if PurePosixPath(file.path).name.startswith("test_")
            and file.path.casefold().endswith(".py")
        ]
        if not test_files:
            raise ValueError("successful candidate did not include reviewable test files")

        return CandidateReviewBundle(
            tool_name=spec.name,
            spec_sha256=spec_hash,
            job_id=job_id,
            candidate_sha256=candidate.candidate_sha256,
            candidate_summary=candidate.payload.summary,
            candidate_files=[file.path for file in candidate.payload.files],
            test_files=test_files,
            candidate_patch=candidate_patch(candidate.payload),
            declared_risks=candidate.payload.risks,
            static_analysis=static_report,
            execution=execution,
            attempts=list(attempts),
        )

    def _record_audit(
        self,
        *,
        spec: ToolBuildSpec,
        spec_hash: str,
        lifecycle_id: str,
        budget_session_id: str,
        client_name: str,
        started_at: datetime,
        attempts: list[CandidateAttemptReport],
        candidate: ToolCandidateResult | None,
        review: CandidateReviewBundle | None,
        result: CandidateLifecycleResult | None,
    ) -> None:
        if self.audit_store is None:
            return
        source_chars = 0
        test_chars = 0
        source_bytes = 0
        test_bytes = 0
        file_count = 0
        candidate_hash = None
        if candidate is not None:
            candidate_hash = candidate.candidate_sha256
            file_count = len(candidate.payload.files)
            for file in candidate.payload.files:
                if PurePosixPath(file.path).name.startswith("test_"):
                    test_chars += len(file.content)
                    test_bytes += len(file.content.encode("utf-8"))
                else:
                    source_chars += len(file.content)
                    source_bytes += len(file.content.encode("utf-8"))
        review_summary_chars = 0
        review_summary_bytes = 0
        patch_chars = 0
        patch_bytes = 0
        final_job_id = attempts[-1].job_id if attempts else None
        if review is not None:
            summary = review_summary(review, spec)
            serialized_summary = summary.model_dump_json(exclude_none=True)
            review_summary_chars = len(serialized_summary)
            review_summary_bytes = len(serialized_summary.encode("utf-8"))
            patch_chars = len(review.candidate_patch)
            patch_bytes = len(review.candidate_patch.encode("utf-8"))
            final_job_id = review.job_id
        serialized_spec = spec.model_dump_json(exclude_none=True)
        self.audit_store.record_lifecycle(
            LifecycleAuditEvent(
                lifecycle_id=lifecycle_id,
                budget_session_id=budget_session_id,
                client_name=client_name,
                model=spec.model,
                tool_name=spec.name,
                spec_sha256=spec_hash,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                status=(result.status if result is not None else CandidateLifecycleStatus.FAILED),
                spec_total_chars=len(serialized_spec),
                purpose_chars=len(spec.purpose),
                input_contract_chars=len(spec.input_contract),
                output_contract_chars=len(spec.output_contract),
                fixture_notes_chars=len(spec.fixture_notes or ""),
                acceptance_tests_chars=sum(len(item) for item in spec.acceptance_tests),
                spec_total_bytes=len(serialized_spec.encode("utf-8")),
                purpose_bytes=len(spec.purpose.encode("utf-8")),
                input_contract_bytes=len(spec.input_contract.encode("utf-8")),
                output_contract_bytes=len(spec.output_contract.encode("utf-8")),
                fixture_notes_bytes=len((spec.fixture_notes or "").encode("utf-8")),
                acceptance_tests_bytes=sum(
                    len(item.encode("utf-8")) for item in spec.acceptance_tests
                ),
                candidate_source_chars=source_chars,
                candidate_test_chars=test_chars,
                candidate_source_bytes=source_bytes,
                candidate_test_bytes=test_bytes,
                candidate_file_count=file_count,
                review_summary_chars=review_summary_chars,
                patch_chars=patch_chars,
                review_summary_bytes=review_summary_bytes,
                patch_bytes=patch_bytes,
                attempt_count=len(attempts),
                repair_count=max(0, len(attempts) - 1),
                final_job_id=final_job_id,
                final_candidate_sha256=candidate_hash,
            )
        )
