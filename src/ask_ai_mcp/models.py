"""Strict domain models shared by policy, audit, and MCP tools."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects accidental or model-hallucinated fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RuntimeKind(StrEnum):
    PYTHON = "python"
    POWERSHELL = "powershell"


class ExecutionContract(StrEnum):
    JSON_FILES_V1 = "json_files_v1"


class ToolCategory(StrEnum):
    DOCUMENT_PARSER = "document_parser"
    TABLE_PROCESSOR = "table_processor"
    FORMAT_VALIDATOR = "format_validator"
    OCR_PIPELINE = "ocr_pipeline"
    TEST_UTILITY = "test_utility"
    CODE_ASSIST = "code_assist"


class DeepSeekModel(StrEnum):
    FLASH = "deepseek-v4-flash"
    PRO = "deepseek-v4-pro"


class PricingBand(StrEnum):
    STANDARD = "standard"
    PEAK = "peak"


class BudgetState(StrEnum):
    ACTIVE = "active"
    FLASH_EXTENSION_REQUIRED = "flash_extension_required"
    PRO_AUTHORIZATION_REQUIRED = "pro_authorization_required"
    PRO_EXTENSION_REQUIRED = "pro_extension_required"
    CLOSED = "closed"


class ReviewMode(StrEnum):
    SUMMARY = "summary"
    FULL = "full"


class WorkflowGuidanceTopic(StrEnum):
    OVERVIEW = "overview"
    BUDGET = "budget"
    BUILD = "build"
    REVIEW = "review"
    APPROVAL = "approval"
    RUN = "run"


class RepairKind(StrEnum):
    STATIC_POLICY = "static_policy"
    SEMANTIC_TEST = "semantic_test"


class CandidateDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    APPROVED_WITH_CHANGES = "approved_with_changes"
    REJECTED = "rejected"


class FindingSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class CandidateJobState(StrEnum):
    STATIC_APPROVED = "static_approved"
    STATIC_REJECTED = "static_rejected"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"
    APPROVED = "approved"


class CandidateLifecycleStatus(StrEnum):
    REVIEW_PENDING = "review_pending"
    FAILED = "failed"


class VerifiedToolExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolCapability(StrEnum):
    READ_SYNTHETIC_INPUTS = "read_synthetic_inputs"
    READ_COPIED_INPUTS = "read_copied_inputs"
    WRITE_DEDICATED_OUTPUT = "write_dedicated_output"


class ToolBuildSpec(StrictModel):
    """Bounded specification supplied by Opus/Sol to the toolsmith."""

    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    category: ToolCategory
    entrypoint: str = Field(
        default="tool.py",
        min_length=4,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*\.py$",
    )
    execution_contract: ExecutionContract = ExecutionContract.JSON_FILES_V1
    purpose: str = Field(min_length=10, max_length=2_000)
    runtime: RuntimeKind = RuntimeKind.PYTHON
    input_contract: str = Field(min_length=10, max_length=4_000)
    output_contract: str = Field(min_length=10, max_length=4_000)
    acceptance_tests: list[str] = Field(min_length=1, max_length=20)
    prohibited_capabilities: list[str] = Field(default_factory=list, max_length=20)
    allowed_packages: list[str] = Field(default_factory=list, max_length=20)
    fixture_notes: str | None = Field(default=None, max_length=4_000)
    model: DeepSeekModel = DeepSeekModel.FLASH


class CandidateFile(StrictModel):
    """One untrusted, relative file returned by the toolsmith."""

    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=100_000)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if "\\" in value or ":" in value:
            raise ValueError("candidate paths must use relative POSIX syntax")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("candidate path escapes its job workspace")
        if path.suffix.casefold() not in {".json", ".md", ".py", ".txt"}:
            raise ValueError("candidate file extension is not allowed")
        return value


class ToolCandidatePayload(StrictModel):
    """Schema-constrained DeepSeek output; still untrusted until reviewed."""

    summary: str = Field(min_length=1, max_length=1_000)
    files: list[CandidateFile] = Field(min_length=1, max_length=20)
    risks: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_payload_bounds(self) -> Self:
        paths = [file.path.casefold() for file in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("candidate file paths must be unique")
        if sum(len(file.content) for file in self.files) > 500_000:
            raise ValueError("candidate payload is too large")
        return self


class ToolCandidateResult(StrictModel):
    """Validated envelope returned to Sol without chain-of-thought content."""

    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    model: DeepSeekModel
    thinking_enabled: bool
    payload: ToolCandidatePayload


class StaticFinding(StrictModel):
    file_path: str = Field(min_length=1, max_length=240)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    severity: FindingSeverity
    message: str = Field(min_length=1, max_length=500)
    line: int | None = Field(default=None, ge=1)


class StaticAnalysisReport(StrictModel):
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    allowed: bool
    scanned_python_files: int = Field(ge=0)
    findings: list[StaticFinding] = Field(default_factory=list, max_length=200)


class CandidateJobManifest(StrictModel):
    job_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state: CandidateJobState
    tool_name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    spec_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    candidate_files: list[str] = Field(min_length=1, max_length=20)
    candidate_file_sha256: dict[str, str] = Field(default_factory=dict, max_length=20)
    execution_backend: str | None = Field(default=None, max_length=64)
    build_model: DeepSeekModel = DeepSeekModel.FLASH
    entrypoint: str | None = Field(default=None, max_length=80)
    execution_contract: ExecutionContract | None = None

    @field_validator("candidate_file_sha256")
    @classmethod
    def validate_candidate_file_sha256(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)
            for digest in value.values()
        ):
            raise ValueError("candidate file hashes must be lowercase SHA-256 values")
        return value


class CandidateExecutionReport(StrictModel):
    job_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    )
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    state: CandidateJobState
    backend: str = Field(min_length=1, max_length=64)
    runner_image: str = Field(min_length=1, max_length=255)
    exit_code: int | None = None
    timed_out: bool = False
    tests_run: int = Field(default=0, ge=0)
    stdout: str = Field(default="", max_length=65_536)
    stderr: str = Field(default="", max_length=65_536)
    output_truncated: bool = False


class CandidateRepairFeedback(StrictModel):
    repair_round: int = Field(ge=1, le=2)
    repair_kind: RepairKind = RepairKind.SEMANTIC_TEST
    reason_codes: list[str] = Field(min_length=1, max_length=20)
    diagnostic_excerpt: str = Field(default="", max_length=8_000)


class CandidateAttemptReport(StrictModel):
    attempt: int = Field(ge=1, le=3)
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    model: DeepSeekModel
    thinking_enabled: bool = True
    repair_kind: RepairKind | None = None
    state: CandidateJobState
    job_id: str | None = Field(default=None, min_length=36, max_length=36)
    static_analysis: StaticAnalysisReport
    execution: CandidateExecutionReport | None = None

    @model_validator(mode="after")
    def validate_execution_identity(self) -> Self:
        if self.execution is not None:
            if self.job_id != self.execution.job_id:
                raise ValueError("attempt and execution job IDs must match")
            if self.candidate_sha256 != self.execution.candidate_sha256:
                raise ValueError("attempt and execution candidate hashes must match")
        return self


class CandidateAttemptSummary(StrictModel):
    attempt: int = Field(ge=1, le=3)
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    model: DeepSeekModel
    thinking_enabled: bool
    repair_kind: RepairKind | None = None
    state: CandidateJobState
    job_id: str | None = Field(default=None, min_length=36, max_length=36)
    static_allowed: bool
    static_finding_codes: list[str] = Field(default_factory=list, max_length=200)
    exit_code: int | None = None
    timed_out: bool = False
    tests_run: int = Field(default=0, ge=0)


class CandidateReviewBundle(StrictModel):
    status: CandidateLifecycleStatus = CandidateLifecycleStatus.REVIEW_PENDING
    tool_name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    spec_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    job_id: str = Field(min_length=36, max_length=36)
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    candidate_summary: str = Field(min_length=1, max_length=1_000)
    candidate_files: list[str] = Field(min_length=1, max_length=20)
    test_files: list[str] = Field(min_length=1, max_length=20)
    candidate_patch: str = Field(min_length=1, max_length=600_000)
    declared_risks: list[str] = Field(default_factory=list, max_length=20)
    static_analysis: StaticAnalysisReport
    execution: CandidateExecutionReport
    attempts: list[CandidateAttemptReport] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_review_identity(self) -> Self:
        if self.job_id != self.execution.job_id:
            raise ValueError("review and execution job IDs must match")
        if self.candidate_sha256 != self.execution.candidate_sha256:
            raise ValueError("review and execution candidate hashes must match")
        return self


class CandidateReviewSummary(StrictModel):
    status: CandidateLifecycleStatus = CandidateLifecycleStatus.REVIEW_PENDING
    tool_name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    spec_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    job_id: str = Field(min_length=36, max_length=36)
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    candidate_summary: str = Field(min_length=1, max_length=1_000)
    candidate_files: list[str] = Field(min_length=1, max_length=20)
    test_files: list[str] = Field(min_length=1, max_length=20)
    declared_risks: list[str] = Field(default_factory=list, max_length=20)
    allowed_packages: list[str] = Field(default_factory=list, max_length=20)
    patch_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    patch_size_bytes: int = Field(ge=1)
    static_allowed: bool
    static_finding_codes: list[str] = Field(default_factory=list, max_length=200)
    tests_run: int = Field(ge=1)
    attempts: list[CandidateAttemptSummary] = Field(min_length=1, max_length=3)
    created_by: str | None = Field(default=None, max_length=64)
    full_review_attestations: list[str] = Field(default_factory=list, max_length=2)
    approval_identities: list[str] = Field(default_factory=list, max_length=2)
    blocking_reasons: list[str] = Field(default_factory=list, max_length=10)
    next_action: str | None = Field(default=None, max_length=240)


class PendingReviewItem(StrictModel):
    job_id: str = Field(min_length=36, max_length=36)
    tool_name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    created_at: datetime
    created_by: str | None = Field(default=None, max_length=64)
    model: DeepSeekModel
    spec_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    registered_versions: list[str] = Field(default_factory=list, max_length=20)
    full_review_attestations: list[str] = Field(default_factory=list, max_length=2)
    approval_identities: list[str] = Field(default_factory=list, max_length=2)
    blocking_reasons: list[str] = Field(default_factory=list, max_length=10)
    next_action: str = Field(min_length=1, max_length=240)


class PendingReviewList(StrictModel):
    items: list[PendingReviewItem] = Field(default_factory=list, max_length=500)


class WorkflowGuidance(StrictModel):
    topic: WorkflowGuidanceTopic
    guidance: list[str] = Field(min_length=1, max_length=12)


class CandidateLifecycleResult(StrictModel):
    lifecycle_id: str = Field(min_length=36, max_length=36)
    budget_session_id: str = Field(min_length=36, max_length=36)
    status: CandidateLifecycleStatus
    tool_name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    spec_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    attempts: list[CandidateAttemptSummary] = Field(min_length=1, max_length=3)
    review_summary: CandidateReviewSummary | None = None
    failure_summary: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.status is CandidateLifecycleStatus.REVIEW_PENDING and self.review_summary is None:
            raise ValueError("review-pending lifecycle result requires a review summary")
        if self.status is CandidateLifecycleStatus.FAILED and self.review_summary is not None:
            raise ValueError("failed lifecycle result cannot contain a review summary")
        return self


class ModelBudgetStatus(StrictModel):
    model: DeepSeekModel
    state: BudgetState
    granted_cny: float = Field(ge=0)
    spent_cny: float = Field(ge=0)
    remaining_cny: float = Field(ge=0)
    overshoot_cny: float = Field(ge=0)
    next_increment_cny: float = Field(default=5.0, ge=5.0, le=5.0)


class BudgetSessionStatus(StrictModel):
    budget_session_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    )
    client_name: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=120)
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    flash: ModelBudgetStatus
    pro: ModelBudgetStatus
    lifecycle_count: int = Field(default=0, ge=0)
    api_call_count: int = Field(default=0, ge=0)


class BudgetSessionOpenCommand(StrictModel):
    label: str | None = Field(default=None, max_length=120)


class BudgetSessionCommand(StrictModel):
    budget_session_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    )


class BudgetIncrementCommand(BudgetSessionCommand):
    model: DeepSeekModel


class ReviewAttestation(StrictModel):
    job_id: str = Field(min_length=36, max_length=36)
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    patch_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    reviewed_by: str = Field(min_length=1, max_length=64)
    reviewed_at: datetime


class SandboxBackendStatus(StrictModel):
    backend: str = Field(min_length=1, max_length=64)
    ready: bool
    container_cli_available: bool
    engine_available: bool
    image_available: bool
    reasons: list[str] = Field(default_factory=list, max_length=20)


class PolicyDecision(StrictModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)


class UsageEvent(StrictModel):
    """Prompt-free audit record for one external model call."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    client_name: str = Field(min_length=1, max_length=64)
    task_kind: str = Field(min_length=1, max_length=64)
    model: DeepSeekModel
    thinking_enabled: bool
    priced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    pricing_band: PricingBand = PricingBand.STANDARD
    pricing_multiplier: float = Field(default=1.0, ge=1.0)
    pricing_schedule_version: str = Field(default="legacy_base", min_length=1, max_length=64)
    cache_hit_price_cny_per_million: float = Field(default=0.0, ge=0)
    cache_miss_price_cny_per_million: float = Field(default=0.0, ge=0)
    output_price_cny_per_million: float = Field(default=0.0, ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    prompt_cache_miss_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated_cost_cny: float = Field(default=0.0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    status: str = Field(min_length=1, max_length=32)
    candidate_hash: str | None = Field(default=None, max_length=128)
    budget_session_id: str | None = Field(default=None, min_length=36, max_length=36)
    lifecycle_id: str | None = Field(default=None, min_length=36, max_length=36)
    request_chars: int = Field(default=0, ge=0)
    response_chars: int = Field(default=0, ge=0)


class LifecycleAuditEvent(StrictModel):
    lifecycle_id: str = Field(min_length=36, max_length=36)
    budget_session_id: str = Field(min_length=36, max_length=36)
    client_name: str = Field(min_length=1, max_length=64)
    model: DeepSeekModel
    tool_name: str = Field(min_length=3, max_length=64)
    spec_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    started_at: datetime
    completed_at: datetime
    status: CandidateLifecycleStatus
    spec_total_chars: int = Field(ge=0)
    purpose_chars: int = Field(ge=0)
    input_contract_chars: int = Field(ge=0)
    output_contract_chars: int = Field(ge=0)
    fixture_notes_chars: int = Field(ge=0)
    acceptance_tests_chars: int = Field(ge=0)
    spec_total_bytes: int = Field(default=0, ge=0)
    purpose_bytes: int = Field(default=0, ge=0)
    input_contract_bytes: int = Field(default=0, ge=0)
    output_contract_bytes: int = Field(default=0, ge=0)
    fixture_notes_bytes: int = Field(default=0, ge=0)
    acceptance_tests_bytes: int = Field(default=0, ge=0)
    candidate_source_chars: int = Field(default=0, ge=0)
    candidate_test_chars: int = Field(default=0, ge=0)
    candidate_source_bytes: int = Field(default=0, ge=0)
    candidate_test_bytes: int = Field(default=0, ge=0)
    candidate_file_count: int = Field(default=0, ge=0)
    review_summary_chars: int = Field(default=0, ge=0)
    patch_chars: int = Field(default=0, ge=0)
    review_summary_bytes: int = Field(default=0, ge=0)
    patch_bytes: int = Field(default=0, ge=0)
    attempt_count: int = Field(default=0, ge=0)
    repair_count: int = Field(default=0, ge=0)
    final_job_id: str | None = Field(default=None, min_length=36, max_length=36)
    final_candidate_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class LifecycleEconomics(StrictModel):
    lifecycle_id: str = Field(min_length=36, max_length=36)
    budget_session_id: str = Field(min_length=36, max_length=36)
    client_name: str = Field(min_length=1, max_length=64)
    model: DeepSeekModel
    tool_name: str = Field(min_length=3, max_length=64)
    completed_at: datetime
    status: CandidateLifecycleStatus
    api_call_count: int = Field(default=0, ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    prompt_cache_miss_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated_cost_cny: float = Field(default=0.0, ge=0)
    structural_bytes_available: bool = False
    spec_total_chars: int = Field(default=0, ge=0)
    spec_total_bytes: int = Field(default=0, ge=0)
    purpose_chars: int = Field(default=0, ge=0)
    purpose_bytes: int = Field(default=0, ge=0)
    input_contract_chars: int = Field(default=0, ge=0)
    input_contract_bytes: int = Field(default=0, ge=0)
    output_contract_chars: int = Field(default=0, ge=0)
    output_contract_bytes: int = Field(default=0, ge=0)
    fixture_notes_chars: int = Field(default=0, ge=0)
    fixture_notes_bytes: int = Field(default=0, ge=0)
    acceptance_tests_chars: int = Field(default=0, ge=0)
    acceptance_tests_bytes: int = Field(default=0, ge=0)
    candidate_source_chars: int = Field(default=0, ge=0)
    candidate_source_bytes: int = Field(default=0, ge=0)
    candidate_test_chars: int = Field(default=0, ge=0)
    candidate_test_bytes: int = Field(default=0, ge=0)
    patch_chars: int = Field(default=0, ge=0)
    patch_bytes: int = Field(default=0, ge=0)
    spec_to_candidate_source_bytes_ratio: float | None = Field(default=None, ge=0)
    spec_to_candidate_total_bytes_ratio: float | None = Field(default=None, ge=0)


class UsageSummary(StrictModel):
    days: int = Field(ge=1, le=366)
    total_calls: int = Field(ge=0)
    successful_calls: int = Field(ge=0)
    failed_calls: int = Field(ge=0)
    prompt_cache_hit_tokens: int = Field(ge=0)
    prompt_cache_miss_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    estimated_cost_cny: float = Field(ge=0)
    by_model: dict[str, int] = Field(default_factory=dict)
    by_client: dict[str, int] = Field(default_factory=dict)
    by_pricing_band: dict[str, int] = Field(default_factory=dict)
    estimated_cost_cny_by_model: dict[str, float] = Field(default_factory=dict)
    estimated_cost_cny_by_client: dict[str, float] = Field(default_factory=dict)
    estimated_cost_cny_by_pricing_band: dict[str, float] = Field(default_factory=dict)
    current_pricing_band: PricingBand
    peak_pricing_enabled: bool
    pricing_schedule_version: str = Field(min_length=1, max_length=64)
    current_beijing_time: datetime
    lifecycle_count: int = Field(default=0, ge=0)
    recent_lifecycle_economics: list[LifecycleEconomics] = Field(
        default_factory=list, max_length=20
    )


class VerifiedToolRecord(StrictModel):
    """Hash-pinned approval metadata for a generated helper tool."""

    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    version: str = Field(min_length=1, max_length=32)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    source_job_id: str = Field(min_length=36, max_length=36)
    spec_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    runner_image: str = Field(min_length=1, max_length=255)
    tests_run: int = Field(ge=1)
    file_sha256: dict[str, str] = Field(min_length=1, max_length=20)
    runtime: RuntimeKind
    approved_at: datetime
    approved_by: str = Field(min_length=1, max_length=64)
    approval_identities: list[str] = Field(default_factory=list, max_length=2)
    decision: CandidateDecision
    allowed_capabilities: list[ToolCapability] = Field(default_factory=list, max_length=3)
    build_model: DeepSeekModel = DeepSeekModel.FLASH
    entrypoint: str | None = Field(default=None, max_length=80)
    execution_contract: ExecutionContract | None = None

    @field_validator("file_sha256")
    @classmethod
    def validate_file_sha256(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)
            for digest in value.values()
        ):
            raise ValueError("verified file hashes must be lowercase SHA-256 values")
        return value

    @model_validator(mode="after")
    def normalize_approval_identities(self) -> Self:
        identities = list(dict.fromkeys([self.approved_by, *self.approval_identities]))
        if len(identities) > 2:
            raise ValueError("at most two desktop approval identities are supported")
        self.approval_identities = identities
        return self


class CandidateApprovalRequest(StrictModel):
    job_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    )
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
    approved_by: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]+$")
    decision: CandidateDecision
    allowed_capabilities: list[ToolCapability] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def require_approval_decision(self) -> Self:
        if self.decision not in {
            CandidateDecision.APPROVED,
            CandidateDecision.APPROVED_WITH_CHANGES,
        }:
            raise ValueError("promotion requires an approval decision")
        return self


class CandidateApprovalCommand(StrictModel):
    """Controller-supplied fields for a clean, exact-hash approval."""

    job_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    )
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
    allowed_capabilities: list[ToolCapability] = Field(default_factory=list, max_length=3)


class RegisteredToolSummary(StrictModel):
    record: VerifiedToolRecord
    runnable: bool
    blocking_reasons: list[str] = Field(default_factory=list, max_length=10)
    execution_count: int = Field(default=0, ge=0)


class RegisteredToolList(StrictModel):
    tools: list[RegisteredToolSummary] = Field(default_factory=list, max_length=500)


class VerifiedToolExecutionCommand(StrictModel):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    input_files: list[str] = Field(default_factory=list, max_length=50)
    parameters_json: str = Field(default="{}", max_length=65_536)

    @field_validator("input_files")
    @classmethod
    def validate_input_file_strings(cls, value: list[str]) -> list[str]:
        if any(not path.strip() or len(path) > 1_024 for path in value):
            raise ValueError("input file paths must be non-empty and at most 1024 characters")
        if len({path.casefold() for path in value}) != len(value):
            raise ValueError("input file paths must be unique")
        return value

    @field_validator("parameters_json")
    @classmethod
    def validate_parameters_json(cls, value: str) -> str:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("parameters_json must contain valid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("parameters_json must contain one JSON object")
        return value


class VerifiedInputArtifact(StrictModel):
    staged_name: str = Field(min_length=1, max_length=120)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class VerifiedOutputArtifact(StrictModel):
    relative_path: str = Field(min_length=1, max_length=240)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class VerifiedToolContainerReport(StrictModel):
    run_id: str = Field(min_length=36, max_length=36)
    exit_code: int | None = None
    timed_out: bool = False
    stdout: str = Field(default="", max_length=65_536)
    stderr: str = Field(default="", max_length=65_536)
    output_truncated: bool = False
    backend: str = Field(min_length=1, max_length=64)
    runner_image: str = Field(min_length=1, max_length=255)


class VerifiedToolExecutionReport(StrictModel):
    run_id: str = Field(min_length=36, max_length=36)
    status: VerifiedToolExecutionStatus
    tool_name: str = Field(min_length=3, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    executed_by: str = Field(min_length=1, max_length=64)
    started_at: datetime
    completed_at: datetime
    backend: str = Field(min_length=1, max_length=64)
    runner_image: str = Field(min_length=1, max_length=255)
    exit_code: int | None = None
    timed_out: bool = False
    output_truncated: bool = False
    input_artifacts: list[VerifiedInputArtifact] = Field(default_factory=list, max_length=50)
    output_artifacts: list[VerifiedOutputArtifact] = Field(default_factory=list, max_length=100)
    output_directory: str = Field(min_length=1, max_length=1_024)
    stdout: str = Field(default="", max_length=65_536)
    stderr: str = Field(default="", max_length=65_536)
    failure_reason: str | None = Field(default=None, max_length=500)


class H3ResolutionPreset(StrEnum):
    LANDSCAPE_480P = "landscape_480p"
    PORTRAIT_480P = "portrait_480p"
    SQUARE_480P = "square_480p"


class H3VisualProfile(StrEnum):
    ANIME = "anime"
    REALISTIC = "realistic"


class H3InterpolationModel(StrEnum):
    NONE = "none"
    RIFE_4_26 = "rife_4_26"
    FILM = "film"


class H3UpscaleModel(StrEnum):
    NONE = "none"
    ANIME_VIDEO = "anime_video"
    SEEDVR2_3B = "seedvr2_3b"


class H3JobState(StrEnum):
    QUEUED_OR_RUNNING = "queued_or_running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class H3GenerationCommand(StrictModel):
    """A bounded local MiniMax H3 FL2VA generation request."""

    prompt: str = Field(min_length=10, max_length=12_000)
    resolution: H3ResolutionPreset = H3ResolutionPreset.LANDSCAPE_480P
    duration_seconds: float = Field(default=5.0, ge=4.0, le=15.0)
    seed: int = Field(default=0, ge=0, le=9_007_199_254_740_991)
    first_frame: str | None = Field(default=None, max_length=1_024)
    last_frame: str | None = Field(default=None, max_length=1_024)

    @model_validator(mode="after")
    def require_first_frame_before_last_frame(self) -> Self:
        if self.last_frame is not None and self.first_frame is None:
            raise ValueError("last_frame requires first_frame")
        return self


class H3BackendStatus(StrictModel):
    backend_url: str = Field(min_length=1, max_length=255)
    reachable: bool
    comfyui_version: str | None = Field(default=None, max_length=120)
    device_name: str | None = Field(default=None, max_length=240)
    required_models: list[str] = Field(min_length=4, max_length=4)
    missing_models: list[str] = Field(default_factory=list, max_length=4)
    ready: bool
    postprocess_models: list[str] = Field(default_factory=list, max_length=5)
    missing_postprocess_models: list[str] = Field(default_factory=list, max_length=5)
    postprocess_ready: bool = False
    detail: str = Field(min_length=1, max_length=500)


class H3PostprocessCommand(StrictModel):
    """A deterministic post-processing request for one selected H3 original."""

    source_video: str = Field(min_length=1, max_length=1_024)
    visual_profile: H3VisualProfile
    interpolation: H3InterpolationModel = H3InterpolationModel.NONE
    upscale: H3UpscaleModel = H3UpscaleModel.NONE
    target_fps: int = Field(default=24)
    target_short_side: int = Field(default=480)
    seed: int = Field(default=0, ge=0, le=9_007_199_254_740_991)

    @model_validator(mode="after")
    def validate_postprocess_combination(self) -> Self:
        if self.target_fps not in {24, 48, 72}:
            raise ValueError("target_fps must be 24, 48, or 72")
        if self.target_short_side not in {480, 720, 1080}:
            raise ValueError("target_short_side must be 480, 720, or 1080")
        if self.interpolation is H3InterpolationModel.NONE and self.target_fps != 24:
            raise ValueError("target_fps above 24 requires an interpolation model")
        if self.interpolation is not H3InterpolationModel.NONE and self.target_fps == 24:
            raise ValueError("an interpolation model requires target_fps 48 or 72")
        if self.upscale is H3UpscaleModel.NONE and self.target_short_side != 480:
            raise ValueError("a larger target_short_side requires an upscale model")
        if self.upscale is not H3UpscaleModel.NONE and self.target_short_side == 480:
            raise ValueError("an upscale model requires target_short_side 720 or 1080")
        if self.visual_profile is H3VisualProfile.ANIME:
            if self.interpolation is H3InterpolationModel.FILM:
                raise ValueError("anime profile supports RIFE interpolation")
            if self.upscale is H3UpscaleModel.SEEDVR2_3B:
                raise ValueError("anime profile supports AnimeVideo upscaling")
        if (
            self.visual_profile is H3VisualProfile.REALISTIC
            and self.upscale is H3UpscaleModel.ANIME_VIDEO
        ):
            raise ValueError("realistic profile supports SeedVR2 upscaling")
        if self.interpolation is H3InterpolationModel.NONE and self.upscale is H3UpscaleModel.NONE:
            raise ValueError("post-processing must enable interpolation or upscaling")
        return self


class H3PostprocessSubmission(StrictModel):
    prompt_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$")
    state: H3JobState = H3JobState.QUEUED_OR_RUNNING
    source_video: str = Field(min_length=1, max_length=1_024)
    visual_profile: H3VisualProfile
    interpolation: H3InterpolationModel
    upscale: H3UpscaleModel
    target_fps: int = Field(ge=24, le=72)
    target_short_side: int = Field(ge=480, le=1080)


class H3JobSubmission(StrictModel):
    prompt_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$")
    state: H3JobState = H3JobState.QUEUED_OR_RUNNING
    resolution: H3ResolutionPreset
    width: int = Field(ge=32, le=1_344)
    height: int = Field(ge=32, le=1_344)
    requested_duration_seconds: float = Field(ge=4.0, le=15.0)
    frame_count: int = Field(ge=5, le=400)
    seed: int = Field(ge=0)


class H3OutputAsset(StrictModel):
    filename: str = Field(min_length=1, max_length=255)
    subfolder: str = Field(default="", max_length=512)
    storage_type: str = Field(default="output", min_length=1, max_length=32)
    local_path: str | None = Field(default=None, max_length=1_024)
    view_url: str = Field(min_length=1, max_length=2_048)


class H3JobReport(StrictModel):
    prompt_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$")
    state: H3JobState
    assets: list[H3OutputAsset] = Field(default_factory=list, max_length=20)
    detail: str = Field(min_length=1, max_length=2_000)
