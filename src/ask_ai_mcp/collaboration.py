"""Prompt-free collaboration state shared by Claude and Codex desktops."""

from __future__ import annotations

from pydantic import ValidationError

from ask_ai_mcp.models import (
    CandidateJobManifest,
    CandidateReviewBundle,
    CandidateReviewSummary,
    DeepSeekModel,
    PendingReviewItem,
    PendingReviewList,
    ToolCapability,
    VerifiedToolRecord,
)
from ask_ai_mcp.promotion import VerifiedToolRegistry
from ask_ai_mcp.review import CandidateReviewError, CandidateReviewRepository
from ask_ai_mcp.review_attestation import ReviewAttestationStore
from ask_ai_mcp.usage import UsageStore

DESKTOP_IDENTITIES = frozenset({"claude_desktop", "codex_desktop"})


class ReviewCollaborationService:
    """Join persisted review, attestation, registry, and lifecycle metadata."""

    def __init__(
        self,
        *,
        repository: CandidateReviewRepository,
        attestations: ReviewAttestationStore,
        registry: VerifiedToolRegistry,
        usage: UsageStore,
    ) -> None:
        self.repository = repository
        self.attestations = attestations
        self.registry = registry
        self.usage = usage

    def enrich_summary(self, summary: CandidateReviewSummary) -> CandidateReviewSummary:
        review = self.repository.load(summary.job_id)
        records = self._records_for_job(summary.job_id)
        state = self._state(review, records)
        return summary.model_copy(
            update={
                "created_by": self.usage.client_for_job(summary.job_id),
                **state,
            }
        )

    def list_pending(self) -> PendingReviewList:
        items: list[PendingReviewItem] = []
        records_by_job: dict[str, list[VerifiedToolRecord]] = {}
        for record in self.registry.list_records():
            records_by_job.setdefault(record.source_job_id, []).append(record)

        for review_path in sorted(self.repository.jobs_root.glob("*/control/review.json")):
            job_id = review_path.parent.parent.name
            try:
                review = self.repository.load(job_id)
                manifest = CandidateJobManifest.model_validate_json(
                    (review_path.parent / "manifest.json").read_text(encoding="utf-8")
                )
            except (CandidateReviewError, OSError, ValidationError):
                continue
            records = records_by_job.get(job_id, [])
            state = self._state(review, records)
            if records and "dual_desktop_approval_required" not in state["blocking_reasons"]:
                continue
            items.append(
                PendingReviewItem(
                    job_id=job_id,
                    tool_name=review.tool_name,
                    created_at=manifest.created_at,
                    created_by=self.usage.client_for_job(job_id),
                    model=review.attempts[-1].model,
                    spec_sha256=review.spec_sha256,
                    candidate_sha256=review.candidate_sha256,
                    registered_versions=sorted({record.version for record in records}),
                    **state,
                )
            )
        items.sort(key=lambda item: item.created_at, reverse=True)
        return PendingReviewList(items=items)

    def _records_for_job(self, job_id: str) -> list[VerifiedToolRecord]:
        return [record for record in self.registry.list_records() if record.source_job_id == job_id]

    def _state(
        self,
        review: CandidateReviewBundle,
        records: list[VerifiedToolRecord],
    ) -> dict[str, list[str] | str]:
        attestations = self.attestations.list_identities(review)
        approval_identities = sorted(
            {identity for record in records for identity in record.approval_identities}
        )
        requires_dual = any(self._requires_dual(record) for record in records)
        if not records:
            blocking_reasons = ["candidate_not_registered"]
            if review.attempts[-1].model is DeepSeekModel.PRO:
                missing = sorted(DESKTOP_IDENTITIES - set(attestations))
                next_action = self._review_then_approve_action(missing)
            else:
                next_action = (
                    "Review the exact candidate as needed, then approve its exact hash and "
                    "least-privilege capabilities."
                )
        elif requires_dual and set(approval_identities) != DESKTOP_IDENTITIES:
            blocking_reasons = ["dual_desktop_approval_required"]
            missing_approvals = sorted(DESKTOP_IDENTITIES - set(approval_identities))
            missing_reviews = [
                identity for identity in missing_approvals if identity not in attestations
            ]
            next_action = (
                self._review_then_approve_action(missing_reviews)
                if missing_reviews
                else "Missing desktop must approve the exact candidate hash."
            )
        else:
            blocking_reasons = []
            next_action = "No collaboration review action is pending."
        return {
            "full_review_attestations": attestations,
            "approval_identities": approval_identities,
            "blocking_reasons": blocking_reasons,
            "next_action": next_action,
        }

    @staticmethod
    def _requires_dual(record: VerifiedToolRecord) -> bool:
        return (
            record.build_model is DeepSeekModel.PRO
            or ToolCapability.WRITE_DEDICATED_OUTPUT in record.allowed_capabilities
        )

    @staticmethod
    def _review_then_approve_action(missing_identities: list[str]) -> str:
        target = ", ".join(missing_identities) if missing_identities else "each desktop"
        return (
            f"{target} must load review_tool_candidate(mode=full), then approve the exact "
            "candidate hash."
        )
