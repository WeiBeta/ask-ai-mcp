"""Auditable proof that one desktop loaded an exact full candidate patch."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ask_ai_mcp.models import CandidateReviewBundle, ReviewAttestation
from ask_ai_mcp.review import patch_sha256
from ask_ai_mcp.usage import default_usage_db_path


class ReviewAttestationError(RuntimeError):
    """Raised when a full-review proof is missing or does not match."""


class ReviewAttestationStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_usage_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_attestations (
                    job_id TEXT NOT NULL,
                    candidate_sha256 TEXT NOT NULL,
                    patch_sha256 TEXT NOT NULL,
                    reviewed_by TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, candidate_sha256, patch_sha256, reviewed_by)
                )
                """
            )

    def record(
        self,
        review: CandidateReviewBundle,
        *,
        client_name: str,
    ) -> ReviewAttestation:
        attestation = ReviewAttestation(
            job_id=review.job_id,
            candidate_sha256=review.candidate_sha256,
            patch_sha256=patch_sha256(review.candidate_patch),
            reviewed_by=client_name,
            reviewed_at=datetime.now(UTC),
        )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO review_attestations (
                    job_id, candidate_sha256, patch_sha256, reviewed_by, reviewed_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id, candidate_sha256, patch_sha256, reviewed_by)
                DO UPDATE SET reviewed_at = excluded.reviewed_at
                """,
                (
                    attestation.job_id,
                    attestation.candidate_sha256,
                    attestation.patch_sha256,
                    attestation.reviewed_by,
                    attestation.reviewed_at.isoformat(),
                ),
            )
        return attestation

    def require(
        self,
        review: CandidateReviewBundle,
        *,
        client_name: str,
    ) -> ReviewAttestation:
        digest = patch_sha256(review.candidate_patch)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM review_attestations
                WHERE job_id = ? AND candidate_sha256 = ?
                  AND patch_sha256 = ? AND reviewed_by = ?
                """,
                (review.job_id, review.candidate_sha256, digest, client_name),
            ).fetchone()
        if row is None:
            raise ReviewAttestationError(
                "full_review_required: load review_tool_candidate with mode=full "
                "for this exact candidate before approval"
            )
        return ReviewAttestation(
            job_id=str(row["job_id"]),
            candidate_sha256=str(row["candidate_sha256"]),
            patch_sha256=str(row["patch_sha256"]),
            reviewed_by=str(row["reviewed_by"]),
            reviewed_at=datetime.fromisoformat(str(row["reviewed_at"])),
        )
