"""Strict domain models shared by policy, audit, and MCP tools."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


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


class ModelProvider(StrEnum):
    """Execution provider identity kept separate from a model name or tier."""

    DEEPSEEK = "deepseek"
    OPENCODE = "opencode"
    LOCAL_QWEN = "local_qwen"
    UNKNOWN = "unknown"


class PricingBand(StrEnum):
    STANDARD = "standard"
    OFF_PEAK = "off_peak"
    PEAK = "peak"


class UsageCostSource(StrEnum):
    LOCAL_ESTIMATE = "local_estimate"
    PROVIDER_REPORTED = "provider_reported"


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
    USAGE = "usage"
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


class CandidateTextEdit(StrictModel):
    """One exact, bounded replacement inside an existing candidate file."""

    file_path: str = Field(min_length=1, max_length=240)
    old_text: str = Field(min_length=1, max_length=4_000)
    new_text: str = Field(max_length=4_000)

    @field_validator("file_path")
    @classmethod
    def validate_python_path(cls, value: str) -> str:
        validated = CandidateFile.validate_relative_path(value)
        if not validated.casefold().endswith(".py"):
            raise ValueError("static repair edits are limited to Python files")
        return validated

    @model_validator(mode="after")
    def validate_changed_text(self) -> Self:
        if self.old_text == self.new_text:
            raise ValueError("static repair edit must change text")
        return self


class CandidatePatchDraftPayload(StrictModel):
    """Untrusted edit set before the controller binds it to a candidate hash."""

    summary: str = Field(default="Static policy repair.", min_length=1, max_length=500)
    edits: list[CandidateTextEdit] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_patch_bounds(self) -> Self:
        if sum(len(edit.old_text) + len(edit.new_text) for edit in self.edits) > 12_000:
            raise ValueError("static repair patch is too large")
        return self


class CandidatePatchPayload(CandidatePatchDraftPayload):
    """Controller-bound static-policy repair patch."""

    base_candidate_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )


class ToolCandidateResult(StrictModel):
    """Validated envelope returned to Sol without chain-of-thought content."""

    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    model: DeepSeekModel
    provider: ModelProvider = ModelProvider.DEEPSEEK
    provider_model_id: str | None = Field(default=None, min_length=1, max_length=255)
    provider_runtime: str | None = Field(default=None, min_length=1, max_length=120)
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
    build_provider: ModelProvider = ModelProvider.DEEPSEEK
    build_model: DeepSeekModel = DeepSeekModel.FLASH
    build_model_id: str | None = Field(default=None, min_length=1, max_length=255)
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
    provider: ModelProvider = ModelProvider.DEEPSEEK
    provider_model_id: str | None = Field(default=None, min_length=1, max_length=255)
    provider_runtime: str | None = Field(default=None, min_length=1, max_length=120)
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
    provider: ModelProvider = ModelProvider.DEEPSEEK
    provider_model_id: str | None = Field(default=None, min_length=1, max_length=255)
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
    audit_scope_id: str = Field(min_length=36, max_length=36)
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
    model: str = Field(min_length=1, max_length=128)
    provider: ModelProvider = ModelProvider.DEEPSEEK
    provider_model_id: str | None = Field(default=None, min_length=1, max_length=128)
    provider_runtime: str | None = Field(default=None, min_length=1, max_length=64)
    provider_account: str | None = Field(default=None, min_length=1, max_length=64)
    provider_subscription_id: str | None = Field(default=None, min_length=1, max_length=128)
    thinking_enabled: bool
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=32)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
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
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0)
    provider_reported_cost_usd: float | None = Field(default=None, ge=0)
    cost_source: UsageCostSource = UsageCostSource.LOCAL_ESTIMATE
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


class OpenCodeGoLimitWindow(StrictModel):
    """Local advisory ledger for one documented OpenCode Go limit window."""

    window: str = Field(min_length=1, max_length=32)
    spent_usd: float = Field(ge=0)
    estimated_spent_usd: float = Field(default=0.0, ge=0)
    provider_reported_spent_usd: float = Field(default=0.0, ge=0)
    provider_reported_call_count: int = Field(default=0, ge=0)
    total_call_count: int = Field(default=0, ge=0)
    limit_usd: float = Field(gt=0)
    remaining_usd: float = Field(ge=0)
    estimated: bool = True


class OpenCodeGoModelAllowance(StrictModel):
    model_id: str = Field(min_length=1, max_length=128)
    spent_usd: float = Field(ge=0)
    estimated_spent_usd: float = Field(default=0.0, ge=0)
    provider_reported_spent_usd: float = Field(default=0.0, ge=0)
    limit_usd: float = Field(gt=0)
    remaining_usd: float = Field(ge=0)
    effective_remaining_usd: float = Field(ge=0)
    estimated: bool = True


class OpenCodeGoAccountUsage(StrictModel):
    """Prompt-free OpenCode Go usage grouped by an explicit account profile."""

    account: str = Field(min_length=1, max_length=64)
    subscription_id: str = Field(min_length=1, max_length=128)
    current_utc_day_spent_usd: float = Field(default=0.0, ge=0)
    daily_pace_target_usd: float = Field(default=2.0, gt=0)
    above_daily_pace: bool = False
    daily_pace_is_hard_limit: bool = False
    windows: list[OpenCodeGoLimitWindow] = Field(default_factory=list, max_length=8)
    rolling_30d_by_model_usd: dict[str, float] = Field(default_factory=dict)
    model_allowances: list[OpenCodeGoModelAllowance] = Field(default_factory=list, max_length=32)
    catalog_version: str = Field(min_length=1, max_length=64)
    catalog_effective_at: datetime
    catalog_source_url: str = Field(min_length=1, max_length=512)
    estimated: bool = True


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
    estimated_cost_usd: float = Field(default=0.0, ge=0)
    by_model: dict[str, int] = Field(default_factory=dict)
    by_provider: dict[str, int] = Field(default_factory=dict)
    by_client: dict[str, int] = Field(default_factory=dict)
    by_pricing_band: dict[str, int] = Field(default_factory=dict)
    estimated_cost_cny_by_model: dict[str, float] = Field(default_factory=dict)
    estimated_cost_cny_by_client: dict[str, float] = Field(default_factory=dict)
    estimated_cost_cny_by_pricing_band: dict[str, float] = Field(default_factory=dict)
    estimated_cost_usd_by_provider_model: dict[str, float] = Field(default_factory=dict)
    opencode_go_accounts: list[OpenCodeGoAccountUsage] = Field(default_factory=list, max_length=16)
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


class SourceExtractionProfile(StrEnum):
    DOCUMENT_EVIDENCE = "document_evidence"
    VISUAL_STRUCTURE = "visual_structure"


class SourceDetailLevel(StrEnum):
    COMPACT = "compact"
    STANDARD = "standard"
    DETAILED = "detailed"


class VisualExtractionScope(StrEnum):
    STRUCTURE_INDEX = "structure_index"
    TOPOLOGY = "topology"
    SELECTED_DETAILS = "selected_details"


class SourceJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SourceExtractionCommand(StrictModel):
    """Bounded source-faithful extraction without an arbitrary prompt surface."""

    source_files: list[str] = Field(min_length=1, max_length=20)
    profile: SourceExtractionProfile
    detail_level: SourceDetailLevel = SourceDetailLevel.STANDARD
    visual_scope: VisualExtractionScope = VisualExtractionScope.STRUCTURE_INDEX
    focus_ids: list[str] = Field(default_factory=list, max_length=8)
    focus_region_xywh: list[float] | None = Field(default=None, min_length=4, max_length=4)
    page_start: int | None = Field(default=None, ge=1, le=100_000)
    page_end: int | None = Field(default=None, ge=1, le=100_000)
    language_hint: str | None = Field(
        default=None,
        min_length=2,
        max_length=20,
        pattern=r"^[A-Za-z0-9-]+$",
    )

    @field_validator("source_files")
    @classmethod
    def validate_source_file_strings(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 1_024 for value in values):
            raise ValueError("source file paths must contain 1-1024 nonblank characters")
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("source file paths must be unique")
        return values

    @field_validator("focus_ids")
    @classmethod
    def validate_focus_ids(cls, values: list[str]) -> list[str]:
        pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,39}$")
        if any(not pattern.fullmatch(value) for value in values):
            raise ValueError("focus_ids must be 1-40 character bounded identifiers")
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("focus_ids must be unique")
        return values

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("page_start and page_end must be supplied together")
        if self.page_start is not None and self.page_end < self.page_start:
            raise ValueError("page_end must be at least page_start")
        if self.profile is not SourceExtractionProfile.VISUAL_STRUCTURE:
            if (
                self.visual_scope is not VisualExtractionScope.STRUCTURE_INDEX
                or self.focus_ids
                or self.focus_region_xywh is not None
            ):
                raise ValueError(
                    "visual_scope, focus_ids, and focus_region_xywh "
                    "require visual_structure profile"
                )
        elif self.visual_scope is VisualExtractionScope.SELECTED_DETAILS:
            if not self.focus_ids:
                raise ValueError("selected_details requires at least one focus_id")
            if self.focus_region_xywh is None:
                raise ValueError("selected_details requires focus_region_xywh")
            x, y, width, height = self.focus_region_xywh
            if any(
                not math.isfinite(value) or value < 0 or value > 1
                for value in self.focus_region_xywh
            ):
                raise ValueError("focus_region_xywh values must be normalized to 0-1")
            if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
                raise ValueError("focus_region_xywh must fit within the source")
        elif self.focus_ids or self.focus_region_xywh is not None:
            raise ValueError(
                "focus_ids and focus_region_xywh require selected_details visual_scope"
            )
        return self


class SourceBackendStatus(StrictModel):
    provider: ModelProvider = ModelProvider.LOCAL_QWEN
    configured: bool
    ready: bool
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    runtime: str | None = Field(default=None, min_length=1, max_length=120)
    supported_profiles: list[SourceExtractionProfile] = Field(default_factory=list, max_length=2)
    active_jobs: int = Field(default=0, ge=0, le=1)
    detail: str = Field(min_length=1, max_length=500)


class SourceJobSubmission(StrictModel):
    job_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    )
    state: SourceJobState = SourceJobState.QUEUED
    profile: SourceExtractionProfile
    source_count: int = Field(ge=1, le=20)


class SourceOutputArtifact(StrictModel):
    relative_path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=120)


class SourceJobReport(StrictModel):
    job_id: str = Field(min_length=36, max_length=36)
    state: SourceJobState
    profile: SourceExtractionProfile
    progress_percent: int = Field(ge=0, le=100)
    detail: str = Field(min_length=1, max_length=500)
    output_directory: str | None = Field(default=None, max_length=1_024)
    artifacts: list[SourceOutputArtifact] = Field(default_factory=list, max_length=200)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    failure_kind: str | None = Field(default=None, min_length=1, max_length=120)


class EvidenceKind(StrEnum):
    TEXT = "text"
    TABLE = "table"
    FIGURE = "figure"
    DIAGRAM = "diagram"
    TRANSCRIPT = "transcript"
    TIMELINE_EVENT = "timeline_event"


class EvidenceLocation(StrictModel):
    whole_file: bool = False
    page: int | None = Field(default=None, ge=1, le=100_000)
    slide: int | None = Field(default=None, ge=1, le=100_000)
    sheet: str | None = Field(default=None, min_length=1, max_length=255)
    cell_or_range: str | None = Field(default=None, min_length=1, max_length=120)
    paragraph: int | None = Field(default=None, ge=1, le=10_000_000)
    object_id: str | None = Field(default=None, min_length=1, max_length=255)
    region_xywh: list[float] | None = Field(default=None, min_length=4, max_length=4)
    time_start_seconds: float | None = Field(default=None, ge=0, le=604_800)
    time_end_seconds: float | None = Field(default=None, gt=0, le=604_800)

    @model_validator(mode="after")
    def validate_location(self) -> Self:
        coordinates = (
            self.page,
            self.slide,
            self.sheet,
            self.cell_or_range,
            self.paragraph,
            self.object_id,
            self.region_xywh,
            self.time_start_seconds,
            self.time_end_seconds,
        )
        if not self.whole_file and all(value is None for value in coordinates):
            raise ValueError("evidence location requires a coordinate or whole_file")
        if self.region_xywh is not None:
            x, y, width, height = self.region_xywh
            if any(value < 0 or value > 1 for value in self.region_xywh):
                raise ValueError("region coordinates must be normalized to 0-1")
            if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
                raise ValueError("region must have positive dimensions within the source")
        if (self.time_start_seconds is None) != (self.time_end_seconds is None):
            raise ValueError("evidence time bounds must be supplied together")
        if self.time_start_seconds is not None and self.time_end_seconds <= self.time_start_seconds:
            raise ValueError("evidence end time must be greater than start time")
        return self


class EvidenceRecord(StrictModel):
    evidence_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    source_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    kind: EvidenceKind
    location: EvidenceLocation
    verbatim_text: str | None = Field(default=None, min_length=1, max_length=100_000)
    data: dict[str, JsonValue] = Field(default_factory=dict)
    extraction_method: str = Field(min_length=1, max_length=120)
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_evidence_content(self) -> Self:
        if self.verbatim_text is None and not self.data:
            raise ValueError("evidence requires verbatim_text or structured data")
        return self


class CanonicalEvidenceBundle(StrictModel):
    contract: str = Field(default="canonical_evidence_v1", pattern=r"^canonical_evidence_v1$")
    profile: SourceExtractionProfile
    records: list[EvidenceRecord] = Field(default_factory=list, max_length=10_000)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("records")
    @classmethod
    def require_unique_evidence_ids(cls, records: list[EvidenceRecord]) -> list[EvidenceRecord]:
        identifiers = [record.evidence_id.casefold() for record in records]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("evidence identifiers must be unique")
        return records


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
    duration_seconds: float = Field(
        default=5.0,
        ge=4.0,
        le=15.0,
        description="Requested seconds; 5-15 is recommended for the trained frame range.",
    )
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
