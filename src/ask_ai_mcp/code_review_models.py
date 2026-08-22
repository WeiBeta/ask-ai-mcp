"""Strict contracts for the optional heterogeneous code-review MCP module."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self

from pydantic import Field, field_validator, model_validator

from ask_ai_mcp.models import OpenCodeGoAccountUsage, StrictModel


class CodeReviewModel(StrEnum):
    GLM_5_3 = "glm-5.3"
    KIMI_K3 = "kimi-k3"
    DEEPSEEK_V4_PRO = "deepseek-v4-pro"
    DEEPSEEK_V4_FLASH = "deepseek-v4-flash"


class CodeReviewProfile(StrEnum):
    GENERAL = "general"
    SECURITY = "security"
    CONCURRENCY = "concurrency"
    DATA_INTEGRITY = "data_integrity"


class CodeReviewJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CodeReviewFindingCategory(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    RELIABILITY = "reliability"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    TESTING = "testing"


class CodeReviewSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CodeReviewAdjudicationDecision(StrEnum):
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    DUPLICATE = "duplicate"
    NON_ACTIONABLE = "non_actionable"
    UNCERTAIN = "uncertain"


class CodeReviewRegressionResult(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


def _validate_repo_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise ValueError("file paths must be normalized repository-relative paths")
    return path.as_posix()


class CodeReviewFinding(StrictModel):
    finding_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    category: CodeReviewFindingCategory
    severity: CodeReviewSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    file: str = Field(min_length=1, max_length=512)
    line_start: int = Field(ge=1, le=10_000_000)
    line_end: int = Field(ge=1, le=10_000_000)
    evidence_summary: str = Field(min_length=1, max_length=1_200)
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rationale: str = Field(min_length=1, max_length=2_400)
    suggested_validation_test: str = Field(min_length=1, max_length=1_200)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        return _validate_repo_relative(value)

    @model_validator(mode="after")
    def validate_line_range(self) -> Self:
        if self.line_end < self.line_start or self.line_end - self.line_start > 80:
            raise ValueError("finding line ranges must be ordered and no wider than 80 lines")
        return self


class CodeReviewPayload(StrictModel):
    findings: list[CodeReviewFinding] = Field(default_factory=list, max_length=100)
    omitted_context: list[str] = Field(default_factory=list, max_length=100)
    truncated: bool = False


class CodeReviewSubmitCommand(StrictModel):
    repository_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    base_ref: str | None = Field(default=None, min_length=1, max_length=160)
    head_ref: str | None = Field(default=None, min_length=1, max_length=160)
    patch_file: str | None = Field(default=None, min_length=3, max_length=1_024)
    patch_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    review_profile: CodeReviewProfile
    model: CodeReviewModel
    review_group_id: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    )

    @field_validator("base_ref", "head_ref")
    @classmethod
    def validate_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith("-") or any(character.isspace() for character in value):
            raise ValueError("Git refs cannot begin with '-' or contain whitespace")
        if any(token in value for token in ("..", "@{", "\\", ":")):
            raise ValueError("Git refs contain a prohibited revision expression")
        return value

    @model_validator(mode="after")
    def choose_exactly_one_input_mode(self) -> Self:
        refs = self.base_ref is not None or self.head_ref is not None
        patch = self.patch_file is not None or self.patch_sha256 is not None
        if refs == patch:
            raise ValueError("choose either base/head refs or a hash-pinned patch file")
        if refs and (self.base_ref is None or self.head_ref is None):
            raise ValueError("base_ref and head_ref must be provided together")
        if patch and (self.patch_file is None or self.patch_sha256 is None):
            raise ValueError("patch_file and patch_sha256 must be provided together")
        return self


class CodeReviewSubmission(StrictModel):
    job_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    review_group_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    blind_label: str = Field(pattern=r"^review-[a-f0-9]{8}$")
    state: CodeReviewJobState
    repository_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    diff_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    changed_file_count: int = Field(ge=0)
    changed_line_count: int = Field(ge=0)


class CodeReviewAdjudicationCommand(StrictModel):
    finding_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    decision: CodeReviewAdjudicationDecision
    severity_agreement: bool | None = None
    accepted: bool | None = None
    fixed: bool | None = None
    test_confirmed: bool | None = None
    adjudication_ms: int = Field(default=0, ge=0, le=86_400_000)


class CodeReviewOutcomeCommand(StrictModel):
    finding_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    adopted: bool | None = None
    escaped_defect: bool | None = None
    regression_result: CodeReviewRegressionResult = CodeReviewRegressionResult.UNKNOWN


class CodeReviewStatusCommand(StrictModel):
    job_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=25, ge=1, le=100)
    adjudication: CodeReviewAdjudicationCommand | None = None
    outcome: CodeReviewOutcomeCommand | None = None


class CodeReviewArtifact(StrictModel):
    relative_path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class CodeReviewStatus(StrictModel):
    job_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    review_group_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    blind_label: str = Field(pattern=r"^review-[a-f0-9]{8}$")
    state: CodeReviewJobState
    detail: str = Field(min_length=1, max_length=1_000)
    model_identity_hidden: bool = True
    total_findings: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    next_offset: int | None = Field(default=None, ge=0)
    findings: list[CodeReviewFinding] = Field(default_factory=list, max_length=100)
    omitted_context: list[str] = Field(default_factory=list, max_length=100)
    truncated: bool = False
    artifacts: list[CodeReviewArtifact] = Field(default_factory=list, max_length=20)


class CodeReviewModelAvailability(StrictModel):
    model_id: CodeReviewModel
    available: bool


class CodeReviewMetricSlice(StrictModel):
    dimension: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=128)
    runs: int = Field(ge=0)
    adjudicated_findings: int = Field(ge=0)
    precision: float | None = Field(default=None, ge=0, le=1)
    unique_true_findings: int = Field(ge=0)
    recall_proxy: float | None = Field(default=None, ge=0, le=1)
    escaped_defects: int = Field(default=0, ge=0)
    false_positive_burden: float = Field(ge=0)
    duplicate_rate: float | None = Field(default=None, ge=0, le=1)
    severity_calibration: float | None = Field(default=None, ge=0, le=1)
    test_confirmed_rate: float | None = Field(default=None, ge=0, le=1)
    true_findings_per_kloc: float | None = Field(default=None, ge=0)
    cost_per_accepted_finding_usd: float | None = Field(default=None, ge=0)
    latency_per_accepted_finding_ms: float | None = Field(default=None, ge=0)
    average_latency_ms: float | None = Field(default=None, ge=0)


class CodeReviewMonthlyReport(StrictModel):
    month: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}$")
    model_names_hidden: bool = True
    slices: list[CodeReviewMetricSlice] = Field(default_factory=list, max_length=200)


class CodeReviewBackendStatus(StrictModel):
    configured: bool
    detail: str = Field(min_length=1, max_length=1_000)
    repository_ids: list[str] = Field(default_factory=list, max_length=64)
    state_root: str | None = Field(default=None, max_length=1_024)
    account_alias: str | None = Field(default=None, max_length=64)
    subscription_id: str | None = Field(default=None, max_length=128)
    remote_models_checked: bool
    models: list[CodeReviewModelAvailability] = Field(default_factory=list, max_length=4)
    account_ledger: OpenCodeGoAccountUsage | None = None
    monthly_report: CodeReviewMonthlyReport | None = None
    catalog_version: str = Field(min_length=1, max_length=64)
    catalog_effective_at: datetime
    catalog_source_url: str = Field(min_length=1, max_length=512)
