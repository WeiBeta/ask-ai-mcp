"""Tests for exact full-review attestations."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ask_ai_mcp.review import patch_sha256
from ask_ai_mcp.review_attestation import (
    ReviewAttestationError,
    ReviewAttestationStore,
)

JOB_ID = "52efb642-6d4a-42ea-9bbf-da5197360c77"
CANDIDATE_HASH = "a" * 64


def make_review(*, patch: str = "diff --git a/tool.py b/tool.py\n+print('ok')\n"):
    return SimpleNamespace(
        job_id=JOB_ID,
        candidate_sha256=CANDIDATE_HASH,
        candidate_patch=patch,
    )


def test_full_review_attestation_is_bound_to_exact_patch_and_client(tmp_path) -> None:
    store = ReviewAttestationStore(tmp_path / "usage.db")
    review = make_review()

    recorded = store.record(review, client_name="claude_desktop")

    assert recorded.patch_sha256 == patch_sha256(review.candidate_patch)
    assert store.require(review, client_name="claude_desktop") == recorded
    with pytest.raises(ReviewAttestationError, match="full_review_required"):
        store.require(review, client_name="codex_desktop")


def test_changed_patch_invalidates_prior_attestation(tmp_path) -> None:
    store = ReviewAttestationStore(tmp_path / "usage.db")
    store.record(make_review(), client_name="codex_desktop")

    with pytest.raises(ReviewAttestationError, match="full_review_required"):
        store.require(
            make_review(patch="diff --git a/tool.py b/tool.py\n+print('changed')\n"),
            client_name="codex_desktop",
        )
