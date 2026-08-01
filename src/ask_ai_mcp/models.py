"""Strict domain models shared by policy, audit, and MCP tools."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
    runtime: RuntimeKind
    approved_at: datetime
    approved_by: str = Field(min_length=1, max_length=64)
    decision: CandidateDecision
    allowed_capabilities: list[str] = Field(default_factory=list, max_length=20)
