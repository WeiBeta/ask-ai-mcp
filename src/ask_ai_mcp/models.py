"""Strict domain models shared by policy, audit, and MCP tools."""

from __future__ import annotations

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


class ToolBuildSpec(StrictModel):
    """Bounded specification supplied by Opus/Sol to the toolsmith."""

    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    category: ToolCategory
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
    reason_codes: list[str] = Field(min_length=1, max_length=20)
    diagnostic_excerpt: str = Field(default="", max_length=8_000)


class CandidateAttemptReport(StrictModel):
    attempt: int = Field(ge=1, le=3)
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    model: DeepSeekModel
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


class CandidateReviewBundle(StrictModel):
    status: CandidateLifecycleStatus = CandidateLifecycleStatus.REVIEW_PENDING
    tool_name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    spec_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    job_id: str = Field(min_length=36, max_length=36)
    candidate_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
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


class CandidateLifecycleResult(StrictModel):
    status: CandidateLifecycleStatus
    tool_name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    spec_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    attempts: list[CandidateAttemptReport] = Field(min_length=1, max_length=3)
    review: CandidateReviewBundle | None = None
    failure_summary: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.status is CandidateLifecycleStatus.REVIEW_PENDING and self.review is None:
            raise ValueError("review-pending lifecycle result requires a review bundle")
        if self.status is CandidateLifecycleStatus.FAILED and self.review is not None:
            raise ValueError("failed lifecycle result cannot contain a review bundle")
        return self


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
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    prompt_cache_miss_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated_cost_cny: float = Field(default=0.0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    status: str = Field(min_length=1, max_length=32)
    candidate_hash: str | None = Field(default=None, max_length=128)


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
    decision: CandidateDecision
    allowed_capabilities: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("file_sha256")
    @classmethod
    def validate_file_sha256(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)
            for digest in value.values()
        ):
            raise ValueError("verified file hashes must be lowercase SHA-256 values")
        return value


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
    allowed_capabilities: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_approval_decision(self) -> Self:
        if self.decision not in {
            CandidateDecision.APPROVED,
            CandidateDecision.APPROVED_WITH_CHANGES,
        }:
            raise ValueError("promotion requires an approval decision")
        return self
