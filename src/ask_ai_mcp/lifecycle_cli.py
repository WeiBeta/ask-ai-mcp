"""Opt-in billed smoke test for the complete candidate review lifecycle."""

from __future__ import annotations

import argparse
import sys

from ask_ai_mcp.budget import BudgetStore
from ask_ai_mcp.deepseek import DeepSeekClientError
from ask_ai_mcp.lifecycle import CandidateLifecycle, CandidateLifecycleError
from ask_ai_mcp.models import (
    CandidateLifecycleStatus,
    ToolBuildSpec,
    ToolCategory,
)
from ask_ai_mcp.policy import PolicyViolation
from ask_ai_mcp.sandbox import docker_backend_status


def smoke_spec() -> ToolBuildSpec:
    return ToolBuildSpec(
        name="normalize_synthetic_headers",
        entrypoint="tool.py",
        category=ToolCategory.TEST_UTILITY,
        purpose=("Create a pure Python helper that normalizes synthetic table headers for tests."),
        input_contract=(
            "A Python list of synthetic strings passed directly to a pure function; no files."
        ),
        output_contract=(
            "A new list where surrounding whitespace is removed and text is case-folded."
        ),
        acceptance_tests=[
            "Use stdlib unittest in a test_*.py file; pytest is not available.",
            "normalize_headers([' Name ', 'AGE']) returns ['name', 'age'].",
            "The input list remains unchanged and duplicate headers remain in order.",
            "Use no network, files, environment variables, subprocesses, or external packages.",
        ],
        prohibited_capabilities=[
            "network_access",
            "filesystem_access",
            "environment_access",
            "subprocess_execution",
        ],
        fixture_notes=(
            "All examples are synthetic and contain no user or business data. The entrypoint "
            "run(request, input_dir, output_dir) receives headers in request['headers']."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ask-ai-mcp-lifecycle-smoke")
    parser.add_argument(
        "--confirm-charge",
        action="store_true",
        help="Confirm one initial DeepSeek call and up to two billed repair calls.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not arguments.confirm_charge:
        print("Refusing billed lifecycle smoke without --confirm-charge.", file=sys.stderr)
        return 2

    backend = docker_backend_status()
    if not backend.ready:
        print(
            "Isolated runner is unavailable: " + ",".join(backend.reasons),
            file=sys.stderr,
        )
        return 2

    try:
        budget = BudgetStore().open_session(
            client_name="lifecycle_smoke", label="explicit lifecycle smoke"
        )
        result = CandidateLifecycle().run(
            smoke_spec(),
            client_name="lifecycle_smoke",
            budget_session_id=budget.budget_session_id,
        )
    except (DeepSeekClientError, CandidateLifecycleError, PolicyViolation) as error:
        print(f"Lifecycle smoke failed safely: {error}", file=sys.stderr)
        return 1

    if (
        result.status is not CandidateLifecycleStatus.REVIEW_PENDING
        or result.review_summary is None
    ):
        print(result.failure_summary or "Candidate exhausted its repair limit.", file=sys.stderr)
        return 1

    review = result.review_summary
    print("Lifecycle smoke passed; candidate remains review-pending and unapproved.")
    print(f"Job ID: {review.job_id}")
    print(f"Candidate SHA-256: {review.candidate_sha256}")
    print(f"Attempts: {len(review.attempts)}")
    print(f"Isolated tests run: {review.tests_run}")
    print("Candidate source was not printed and no real source documents were used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
