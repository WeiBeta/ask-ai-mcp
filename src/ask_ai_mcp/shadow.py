"""Offline Qwen shadow evaluation against immutable DeepSeek replay capsules."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import Field

from ask_ai_mcp.hashing import candidate_payload_sha256, tool_spec_sha256
from ask_ai_mcp.lifecycle import CandidateLifecycle
from ask_ai_mcp.models import (
    CandidateExecutionReport,
    CandidateJobState,
    CandidateLifecycleResult,
    CandidateLifecycleStatus,
    ModelProvider,
    StaticAnalysisReport,
    StrictModel,
    ToolCandidatePayload,
    ToolCandidateResult,
)
from ask_ai_mcp.qwen_toolsmith import LocalQwenToolsmithClient
from ask_ai_mcp.replay import ReplayCaptureError, ReplayStore, ToolsmithReplayCapsule
from ask_ai_mcp.sandbox import DockerCandidateExecutor
from ask_ai_mcp.static_policy import analyze_candidate
from ask_ai_mcp.workspace import CandidateWorkspaceManager

SHADOW_SCHEMA_VERSION = "toolsmith_shadow_v1"


class ShadowEvaluationError(RuntimeError):
    """Raised when a replay capsule cannot be graded without inventing evidence."""


class ShadowLifecycleGrade(StrictModel):
    lifecycle_id: str = Field(min_length=36, max_length=36)
    provider: ModelProvider
    status: CandidateLifecycleStatus | None = None
    final_candidate_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    attempt_count: int = Field(ge=0, le=3)
    final_state: CandidateJobState | None = None
    tests_run: int = Field(default=0, ge=0)
    passed_self_tests: bool = False


class ShadowCrossGrade(StrictModel):
    candidate_sha256: str = Field(min_length=64, max_length=64)
    reference_test_sha256: str = Field(min_length=64, max_length=64)
    static_analysis: StaticAnalysisReport
    execution: CandidateExecutionReport | None = None
    passed_reference_tests: bool = False


class ToolsmithShadowComparison(StrictModel):
    schema_version: str = Field(default=SHADOW_SCHEMA_VERSION, pattern=r"^[a-z0-9_]+$")
    compared_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    spec_sha256: str = Field(min_length=64, max_length=64)
    reference_prompt_sha256: str = Field(min_length=64, max_length=64)
    challenger_prompt_sha256: str = Field(min_length=64, max_length=64)
    reference: ShadowLifecycleGrade
    challenger: ShadowLifecycleGrade
    cross_grade: ShadowCrossGrade | None = None
    oracle_strength: str = Field(default="reference_candidate_tests", max_length=80)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class ShadowLifecycleRunner(Protocol):
    def run(
        self,
        spec,
        *,
        client_name: str,
        budget_session_id: str,
        allow_pro: bool = False,
    ) -> CandidateLifecycleResult: ...


class ReferenceTestGrader:
    """Run the challenger implementation against tests captured from the reference."""

    def __init__(
        self,
        *,
        workspace: CandidateWorkspaceManager | None = None,
        executor: DockerCandidateExecutor | None = None,
    ) -> None:
        self.workspace = workspace or CandidateWorkspaceManager()
        self.executor = executor or DockerCandidateExecutor()

    def grade(
        self,
        reference: ToolsmithReplayCapsule,
        challenger: ToolsmithReplayCapsule,
    ) -> ShadowCrossGrade:
        reference_payload = self._final_payload(reference)
        challenger_payload = self._final_payload(challenger)
        reference_tests = [
            file
            for file in reference_payload.files
            if PurePosixPath(file.path).name.startswith("test_")
            and file.path.casefold().endswith(".py")
        ]
        if not reference_tests:
            raise ShadowEvaluationError("reference capsule has no executable test files")
        files = [
            file
            for file in challenger_payload.files
            if not (
                PurePosixPath(file.path).name.startswith("test_")
                and file.path.casefold().endswith(".py")
            )
        ]
        files.extend(reference_tests)
        payload = ToolCandidatePayload(
            summary="Qwen challenger with immutable DeepSeek reference tests.",
            files=files,
            risks=[
                *challenger_payload.risks[:19],
                "reference_tests_are_not_an_independent_gold_oracle",
            ],
        )
        digest = candidate_payload_sha256(payload)
        final_report = challenger.attempts[-1].report
        result = ToolCandidateResult(
            candidate_sha256=digest,
            model=challenger.spec.model,
            provider=final_report.provider,
            provider_model_id=final_report.provider_model_id,
            provider_runtime=final_report.provider_runtime,
            thinking_enabled=final_report.thinking_enabled,
            payload=payload,
        )
        static_report = analyze_candidate(challenger.spec, payload)
        execution = None
        if static_report.allowed:
            job_root, _, _ = self.workspace.stage(spec=challenger.spec, result=result)
            execution = self.executor.execute(job_root)
        test_digest = hashlib.sha256(
            "".join(
                f"{file.path}\0{file.content}\0"
                for file in sorted(reference_tests, key=lambda item: item.path)
            ).encode("utf-8")
        ).hexdigest()
        passed = bool(
            execution
            and execution.state is CandidateJobState.EXECUTED
            and execution.exit_code == 0
            and execution.tests_run > 0
            and not execution.timed_out
        )
        return ShadowCrossGrade(
            candidate_sha256=digest,
            reference_test_sha256=test_digest,
            static_analysis=static_report,
            execution=execution,
            passed_reference_tests=passed,
        )

    @staticmethod
    def _final_payload(capsule: ToolsmithReplayCapsule) -> ToolCandidatePayload:
        if not capsule.attempts:
            raise ShadowEvaluationError("replay capsule contains no parsed candidate")
        return capsule.attempts[-1].candidate


class ShadowComparisonStore:
    """Write one hash-protected comparison beside, but not inside, replay capsules."""

    def __init__(self, replay_root: Path) -> None:
        self.root = replay_root.resolve() / "shadow-comparisons"
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, comparison: ToolsmithShadowComparison) -> Path:
        folder = self.root / comparison.reference.lifecycle_id
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{comparison.challenger.lifecycle_id}.json"
        digest_target = target.with_suffix(".sha256")
        payload = comparison.model_dump_json(indent=2).encode("utf-8")
        if target.exists() and target.read_bytes() != payload:
            raise ReplayCaptureError("immutable shadow comparison already exists with other bytes")
        target.write_bytes(payload)
        digest_target.write_text(
            f"{hashlib.sha256(payload).hexdigest()}  {target.name}\n",
            encoding="ascii",
        )
        return target


class QwenShadowEvaluator:
    """Replay one captured spec through Qwen and record mechanical comparison evidence."""

    def __init__(
        self,
        *,
        replay_store: ReplayStore | None = None,
        lifecycle: ShadowLifecycleRunner | None = None,
        grader: ReferenceTestGrader | None = None,
        comparison_store: ShadowComparisonStore | None = None,
    ) -> None:
        self.replay_store = replay_store or ReplayStore()
        self.lifecycle = lifecycle or CandidateLifecycle(
            client=LocalQwenToolsmithClient(),
            replay_store=self.replay_store,
        )
        self.grader = grader or ReferenceTestGrader()
        self.comparison_store = comparison_store or ShadowComparisonStore(self.replay_store.root)

    def evaluate(self, reference_lifecycle_id: str) -> tuple[ToolsmithShadowComparison, Path]:
        reference = self.replay_store.load(reference_lifecycle_id)
        if not reference.attempts:
            raise ShadowEvaluationError("reference capsule contains no candidate attempts")
        existing = {path.parent.name for path in self.replay_store.root.glob("*/capsule.json")}
        lifecycle_failure: str | None = None
        try:
            result = self.lifecycle.run(
                reference.spec,
                client_name="qwen_shadow",
                budget_session_id=reference.budget_session_id,
                allow_pro=False,
            )
            challenger = self.replay_store.load(result.lifecycle_id)
        except Exception as error:
            lifecycle_failure = type(error).__name__
            challenger = self._new_challenger(existing, reference)
        if tool_spec_sha256(reference.spec) != tool_spec_sha256(challenger.spec):
            raise ShadowEvaluationError("challenger spec differs from the replay reference")
        cross_grade = None
        warnings = ["reference_candidate_tests_are_not_an_independent_gold_oracle"]
        if challenger.attempts:
            cross_grade = self.grader.grade(reference, challenger)
        else:
            warnings.append("challenger_has_no_parsed_candidate")
        if reference.reconstructed:
            warnings.append("reference_replay_was_reconstructed")
        if reference.result is None:
            warnings.append("reference_lifecycle_has_no_terminal_result")
        if reference.failure_kind:
            warnings.append(f"reference_failure_kind:{reference.failure_kind}")
        if not reference.attempts[-1].report.static_analysis.allowed:
            warnings.append("reference_was_static_rejected_under_capture_policy")
        if lifecycle_failure:
            warnings.append(f"challenger_failure_kind:{lifecycle_failure}")
        comparison = ToolsmithShadowComparison(
            spec_sha256=tool_spec_sha256(reference.spec),
            reference_prompt_sha256=reference.prompt_template_sha256,
            challenger_prompt_sha256=challenger.prompt_template_sha256,
            reference=self._grade(reference),
            challenger=self._grade(challenger),
            cross_grade=cross_grade,
            warnings=warnings,
        )
        return comparison, self.comparison_store.write(comparison)

    def _new_challenger(
        self,
        existing: set[str],
        reference: ToolsmithReplayCapsule,
    ) -> ToolsmithReplayCapsule:
        candidates = []
        expected_spec_hash = tool_spec_sha256(reference.spec)
        for path in self.replay_store.root.glob("*/capsule.json"):
            if path.parent.name in existing:
                continue
            try:
                capsule = self.replay_store.load(path.parent.name)
            except ReplayCaptureError:
                continue
            if (
                capsule.client_name == "qwen_shadow"
                and tool_spec_sha256(capsule.spec) == expected_spec_hash
            ):
                candidates.append((path.stat().st_mtime_ns, capsule))
        if not candidates:
            raise ShadowEvaluationError("failed lifecycle did not produce a replay capsule")
        return max(candidates, key=lambda item: item[0])[1]

    @staticmethod
    def _grade(capsule: ToolsmithReplayCapsule) -> ShadowLifecycleGrade:
        if not capsule.attempts:
            return ShadowLifecycleGrade(
                lifecycle_id=capsule.lifecycle_id,
                provider=ModelProvider.UNKNOWN,
                status=capsule.result.status if capsule.result else None,
                attempt_count=0,
            )
        final = capsule.attempts[-1].report
        execution = final.execution
        return ShadowLifecycleGrade(
            lifecycle_id=capsule.lifecycle_id,
            provider=final.provider,
            status=capsule.result.status if capsule.result else None,
            final_candidate_sha256=final.candidate_sha256,
            attempt_count=len(capsule.attempts),
            final_state=final.state,
            tests_run=execution.tests_run if execution else 0,
            passed_self_tests=bool(
                execution
                and execution.state is CandidateJobState.EXECUTED
                and execution.exit_code == 0
                and execution.tests_run > 0
                and not execution.timed_out
            ),
        )
