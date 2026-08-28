"""Strict contracts for the optional bounded coding-candidate MCP."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self

from pydantic import Field, field_validator, model_validator

from ask_ai_mcp.models import OpenCodeGoAccountUsage, ProviderTimeoutStatus, StrictModel

_HOST_PATH = re.compile(r"(?i)(?:[A-Z]:[\\/]+(?:Users|Dev|AI)[\\/])")
_SECRET_TEXT = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*"
    r"[\"'][^\"'\r\n]{12,}[\"']|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


class CodingModel(StrEnum):
    DEEPSEEK_V4_FLASH = "deepseek-v4-flash"
    GLM_5_3_FLASH = "glm-5.3-flash"
    DEEPSEEK_V4_PRO = "deepseek-v4-pro"
    GLM_5_3 = "glm-5.3"
    KIMI_K3 = "kimi-k3"
    GROK_4_6 = "grok-4.6"


DEFAULT_CODING_MODELS = (
    CodingModel.DEEPSEEK_V4_FLASH,
    CodingModel.GLM_5_3_FLASH,
)
ADVANCED_CODING_MODELS = (
    CodingModel.DEEPSEEK_V4_PRO,
    CodingModel.GLM_5_3,
    CodingModel.KIMI_K3,
    CodingModel.GROK_4_6,
)


class CodingTaskKind(StrEnum):
    BUG_FIX = "bug_fix"
    TEST_ADDITION = "test_addition"
    BOUNDED_IMPLEMENTATION = "bounded_implementation"
    DETERMINISTIC_REFACTOR = "deterministic_refactor"


class CodingJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def validate_repo_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise ValueError("paths must be normalized repository-relative paths")
    return path.as_posix()


class CodingSubmitCommand(StrictModel):
    repository_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    base_ref: str = Field(min_length=1, max_length=160)
    task_kind: CodingTaskKind
    model: CodingModel
    target_files: list[str] = Field(min_length=1, max_length=12)
    context_files: list[str] = Field(default_factory=list, max_length=12)
    requirements: list[str] = Field(min_length=1, max_length=20)
    acceptance_tests: list[str] = Field(min_length=1, max_length=20)

    @field_validator("base_ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        if value.startswith("-") or any(character.isspace() for character in value):
            raise ValueError("Git refs cannot begin with '-' or contain whitespace")
        if any(token in value for token in ("..", "@{", "\\", ":")):
            raise ValueError("Git ref contains a prohibited revision expression")
        return value

    @field_validator("target_files", "context_files")
    @classmethod
    def validate_files(cls, values: list[str]) -> list[str]:
        normalized = [validate_repo_relative(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("file paths must be unique")
        return normalized

    @field_validator("requirements", "acceptance_tests")
    @classmethod
    def validate_bounded_text(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 1_200 for value in values):
            raise ValueError("requirements and acceptance tests must be non-empty and bounded")
        if sum(len(value) for value in values) > 12_000:
            raise ValueError("coding specification is too large")
        if any(_HOST_PATH.search(value) or _SECRET_TEXT.search(value) for value in values):
            raise ValueError("coding specification contains a prohibited host path or secret")
        return [value.strip() for value in values]

    @model_validator(mode="after")
    def validate_file_roles(self) -> Self:
        if set(self.target_files) & set(self.context_files):
            raise ValueError("target and context file sets must be disjoint")
        return self


class CodingFileChange(StrictModel):
    file: str = Field(min_length=1, max_length=512)
    original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(max_length=256_000)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        return validate_repo_relative(value)


class CodingCandidatePayload(StrictModel):
    summary: str = Field(min_length=1, max_length=2_000)
    changes: list[CodingFileChange] = Field(min_length=1, max_length=12)
    suggested_tests: list[str] = Field(default_factory=list, max_length=20)
    risks: list[str] = Field(default_factory=list, max_length=20)
    truncated: bool = False

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        paths = [change.file for change in self.changes]
        if len(paths) != len(set(paths)):
            raise ValueError("candidate change paths must be unique")
        if sum(len(change.content) for change in self.changes) > 700_000:
            raise ValueError("candidate content exceeds the bounded output size")
        if any(not value.strip() or len(value) > 1_200 for value in self.suggested_tests):
            raise ValueError("suggested tests must be non-empty and bounded")
        if any(not value.strip() or len(value) > 1_200 for value in self.risks):
            raise ValueError("risks must be non-empty and bounded")
        return self


class CodingSubmission(StrictModel):
    job_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    state: CodingJobState
    repository_id: str
    base_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model: CodingModel
    reasoning_effort: str = "max"
    max_output_tokens: int = Field(default=131_072, ge=1, le=131_072)


class CodingStatusCommand(StrictModel):
    job_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    offset: int = Field(default=0, ge=0, le=2_000_000)
    limit: int = Field(default=12_000, ge=1_000, le=40_000)


class CodingStatus(StrictModel):
    job_id: str
    state: CodingJobState
    detail: str = Field(min_length=1, max_length=1_000)
    repository_id: str
    base_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    model: CodingModel
    reasoning_effort: str = "max"
    max_output_tokens: int = Field(default=131_072, ge=1, le=131_072)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    timeout_phase: str | None = Field(default=None, pattern=r"^(connect|read|write|pool|unknown)$")
    transport_failure_kind: str | None = Field(
        default=None,
        pattern=r"^(REMOTE_PROTOCOL|LOCAL_PROTOCOL|PROXY|CONNECT|READ_IO|WRITE_IO|CLOSE_IO|OTHER)$",
    )
    wire_capture_uid: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")
    progress_source: str = Field(pattern=r"^(local_worker|provider_response|unavailable)$")
    upstream_progress_confirmed: bool = False
    usage_observed: bool = False
    provider_timeout: ProviderTimeoutStatus | None = None
    candidate_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    changed_files: list[str] = Field(default_factory=list, max_length=12)
    summary: str | None = Field(default=None, max_length=2_000)
    suggested_tests: list[str] = Field(default_factory=list, max_length=20)
    risks: list[str] = Field(default_factory=list, max_length=20)
    patch_offset: int = Field(ge=0)
    patch_chunk: str = Field(default="", max_length=40_000)
    next_offset: int | None = Field(default=None, ge=0)


class CodingModelAvailability(StrictModel):
    model_id: CodingModel
    available: bool
    requested_reasoning_effort: str = "max"
    max_output_tokens: int = Field(default=131_072, ge=1, le=131_072)
    standard_price_max_input_tokens: int | None = Field(default=None, ge=1)


class CodingBackendStatus(StrictModel):
    configured: bool
    detail: str = Field(min_length=1, max_length=1_000)
    repository_ids: list[str] = Field(default_factory=list, max_length=64)
    account_uid: str | None = Field(default=None, max_length=64)
    account_alias: str | None = Field(default=None, max_length=64)
    remote_models_checked: bool
    models: list[CodingModelAvailability] = Field(default_factory=list, max_length=6)
    routing_policy_version: str = Field(min_length=1, max_length=64)
    standard_route_order: list[CodingModel] = Field(default_factory=list, max_length=6)
    advanced_route_order: list[CodingModel] = Field(default_factory=list, max_length=6)
    account_ledger: OpenCodeGoAccountUsage | None = None
    catalog_version: str
    catalog_source_url: str
    provider_timeout: ProviderTimeoutStatus
    encrypted_wire_capture_enabled: bool = False
    wire_capture_max_bytes: int = Field(default=104_857_600, ge=1, le=104_857_600)
