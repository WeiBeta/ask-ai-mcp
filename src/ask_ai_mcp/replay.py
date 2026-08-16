"""Opt-in, content-bearing replay capsules for mechanically graded shadow tests."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from platformdirs import user_data_path
from pydantic import Field

from ask_ai_mcp import __version__
from ask_ai_mcp.deepseek import PROMPT_TEMPLATE_VERSION, prompt_template_sha256
from ask_ai_mcp.hashing import candidate_payload_sha256
from ask_ai_mcp.models import (
    CandidateAttemptReport,
    CandidateFile,
    CandidateLifecycleResult,
    CandidateRepairFeedback,
    CandidateReviewBundle,
    StrictModel,
    ToolBuildSpec,
    ToolCandidatePayload,
)
from ask_ai_mcp.review import CandidateReviewRepository
from ask_ai_mcp.workspace import default_jobs_root

REPLAY_CAPTURE_ENV = "ASK_AI_MCP_REPLAY_CAPTURE"
REPLAY_ROOT_ENV = "ASK_AI_MCP_REPLAY_ROOT"
REPLAY_SCHEMA_VERSION = "toolsmith_replay_v1"
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class ReplayCaptureError(RuntimeError):
    """Raised when an immutable replay capsule cannot be safely persisted."""


class ReplayAttempt(StrictModel):
    """One parsed model candidate bound to controller-owned grading evidence."""

    report: CandidateAttemptReport
    candidate: ToolCandidatePayload
    feedback_for_next_attempt: CandidateRepairFeedback | None = None


class ToolsmithReplayCapsule(StrictModel):
    """Self-contained replay input, candidate history, and executable oracle evidence."""

    schema_version: str = Field(default=REPLAY_SCHEMA_VERSION, pattern=r"^[a-z0-9_]+$")
    package_version: str = Field(default=__version__, min_length=1, max_length=32)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lifecycle_id: str = Field(min_length=36, max_length=36)
    budget_session_id: str = Field(min_length=36, max_length=36)
    client_name: str = Field(min_length=1, max_length=64)
    prompt_template_version: str = Field(min_length=1, max_length=120)
    prompt_template_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )
    spec: ToolBuildSpec
    attempts: list[ReplayAttempt] = Field(default_factory=list, max_length=3)
    result: CandidateLifecycleResult | None = None
    failure_kind: str | None = Field(default=None, min_length=1, max_length=120)
    reconstructed: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=20)


class ReplayImportSummary(StrictModel):
    scanned_reviews: int = Field(ge=0)
    imported_capsules: int = Field(ge=0)
    skipped_capsules: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=100)


def replay_capture_enabled() -> bool:
    value = os.environ.get(REPLAY_CAPTURE_ENV, "0")
    return value.strip().casefold() not in _FALSE_VALUES


def default_replay_root() -> Path:
    configured = os.environ.get(REPLAY_ROOT_ENV, "").strip()
    if configured:
        return Path(configured)
    return user_data_path("AskAIMCP", appauthor=False, ensure_exists=True) / "replay"


class ReplayStore:
    """Persist immutable capsules separately from prompt-free operational audit logs."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_replay_root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_environment(cls) -> ReplayStore | None:
        return cls() if replay_capture_enabled() else None

    def write(self, capsule: ToolsmithReplayCapsule) -> Path:
        try:
            UUID(capsule.lifecycle_id)
        except ValueError as error:
            raise ReplayCaptureError("invalid lifecycle identifier") from error
        capsule_root = self._within(self.root / capsule.lifecycle_id)
        capsule_root.mkdir(parents=False, exist_ok=True)
        target = self._within(capsule_root / "capsule.json")
        digest_target = self._within(capsule_root / "capsule.sha256")
        payload = capsule.model_dump_json(indent=2).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()

        if target.exists():
            existing = target.read_bytes()
            if existing != payload:
                raise ReplayCaptureError("immutable replay capsule already exists with other bytes")
            self._verify_digest(target, digest_target)
            return target

        temporary = self._within(capsule_root / f".{uuid4()}.tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)
        digest_target.write_text(f"{digest}  capsule.json\n", encoding="ascii")
        return target

    def load(self, lifecycle_id: str) -> ToolsmithReplayCapsule:
        try:
            UUID(lifecycle_id)
        except ValueError as error:
            raise ReplayCaptureError("invalid lifecycle identifier") from error
        target = self._within(self.root / lifecycle_id / "capsule.json")
        digest_target = self._within(self.root / lifecycle_id / "capsule.sha256")
        self._verify_digest(target, digest_target)
        return ToolsmithReplayCapsule.model_validate_json(target.read_text(encoding="utf-8"))

    @staticmethod
    def _verify_digest(target: Path, digest_target: Path) -> None:
        if not target.is_file() or not digest_target.is_file():
            raise ReplayCaptureError("replay capsule or digest is missing")
        recorded = digest_target.read_text(encoding="ascii").split(maxsplit=1)[0]
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if recorded != actual:
            raise ReplayCaptureError("replay capsule digest mismatch")

    def _within(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ReplayCaptureError("replay path escapes configured root")
        return resolved


class LegacyReplayImporter:
    """Recover exact final candidates from pre-capture review jobs without inventing history."""

    def __init__(
        self,
        *,
        jobs_root: Path | None = None,
        usage_database: Path | None = None,
        replay_store: ReplayStore | None = None,
    ) -> None:
        self.jobs_root = (jobs_root or default_jobs_root()).resolve()
        self.usage_database = usage_database
        self.replay_store = replay_store or ReplayStore()
        self.repository = CandidateReviewRepository(self.jobs_root)

    def import_all(self) -> ReplayImportSummary:
        reviews = sorted(self.jobs_root.glob("*/control/review.json"))
        imported = 0
        skipped = 0
        warnings: list[str] = []
        for review_path in reviews:
            job_id = review_path.parents[1].name
            try:
                capsule = self._capsule_for_job(job_id)
                self.replay_store.write(capsule)
                imported += 1
            except Exception as error:
                skipped += 1
                warnings.append(f"{job_id}:{type(error).__name__}")
        return ReplayImportSummary(
            scanned_reviews=len(reviews),
            imported_capsules=imported,
            skipped_capsules=skipped,
            warnings=warnings,
        )

    def _capsule_for_job(self, job_id: str) -> ToolsmithReplayCapsule:
        review = self.repository.load(job_id)
        job_root = self.jobs_root / job_id
        spec = ToolBuildSpec.model_validate_json(
            (job_root / "control" / "spec.json").read_text(encoding="utf-8")
        )
        payload = self._final_payload(job_root, review)
        if candidate_payload_sha256(payload) != review.candidate_sha256:
            raise ReplayCaptureError("legacy final candidate does not match review hash")
        lifecycle_id, budget_session_id, client_name = self._lifecycle_identity(job_id)
        final_attempt = review.attempts[-1]
        warnings = ["legacy_only_final_candidate", "repair_history_incomplete"]
        if lifecycle_id == job_id:
            warnings.append("lifecycle_identity_unavailable")
        return ToolsmithReplayCapsule(
            lifecycle_id=lifecycle_id,
            budget_session_id=budget_session_id,
            client_name=client_name,
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
            prompt_template_sha256=prompt_template_sha256(),
            spec=spec,
            attempts=[ReplayAttempt(report=final_attempt, candidate=payload)],
            reconstructed=True,
            warnings=warnings,
        )

    @staticmethod
    def _final_payload(job_root: Path, review: CandidateReviewBundle) -> ToolCandidatePayload:
        files = []
        candidate_root = job_root / "candidate"
        for relative in review.candidate_files:
            path = candidate_root / Path(*relative.split("/"))
            files.append(CandidateFile(path=relative, content=path.read_text(encoding="utf-8")))
        return ToolCandidatePayload(
            summary=review.candidate_summary,
            files=files,
            risks=review.declared_risks,
        )

    def _lifecycle_identity(self, job_id: str) -> tuple[str, str, str]:
        fallback_budget = "00000000-0000-4000-8000-000000000000"
        if self.usage_database is None or not self.usage_database.is_file():
            return job_id, fallback_budget, "unknown"
        with sqlite3.connect(self.usage_database) as connection:
            row = connection.execute(
                """
                SELECT lifecycle_id, budget_session_id, client_name
                FROM lifecycle_audit
                WHERE final_job_id = ?
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return job_id, fallback_budget, "unknown"
        return str(row[0]), str(row[1]), str(row[2])
