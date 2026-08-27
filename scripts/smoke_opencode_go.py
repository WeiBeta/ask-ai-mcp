"""Explicitly billed OpenCode Go smoke for the five fixed Coding/Review models."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from platformdirs import user_data_path

from ask_ai_mcp.code_review import CodeReviewManager
from ask_ai_mcp.code_review_models import (
    CodeReviewJobState,
    CodeReviewModel,
    CodeReviewProfile,
    CodeReviewStatusCommand,
    CodeReviewSubmitCommand,
)
from ask_ai_mcp.code_review_store import CodeReviewStore
from ask_ai_mcp.code_review_workspace import (
    CodeReviewRepositoryCatalog,
    CodeReviewSnapshotter,
)
from ask_ai_mcp.coding import CodingManager
from ask_ai_mcp.coding_models import (
    CodingJobState,
    CodingModel,
    CodingStatusCommand,
    CodingSubmitCommand,
    CodingTaskKind,
)
from ask_ai_mcp.coding_workspace import CodingSnapshotter
from ask_ai_mcp.opencode_account import load_opencode_account
from ask_ai_mcp.usage import UsageStore


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout.strip()


def _make_repository(root: Path) -> tuple[str, str]:
    root.mkdir(parents=True, exist_ok=False)
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "smoke@example.invalid")
    _git(root, "config", "user.name", "Ask AI Smoke")
    source = root / "Counter.cs"
    source.write_text(
        "namespace Smoke;\n\n"
        "public static class Counter\n"
        "{\n"
        "    public static int Next(int value) => value + 1;\n"
        "}\n",
        encoding="utf-8",
    )
    _git(root, "add", "Counter.cs")
    _git(root, "commit", "-m", "safe baseline")
    base = _git(root, "rev-parse", "HEAD")
    source.write_text(
        "namespace Smoke;\n\n"
        "public static class Counter\n"
        "{\n"
        "    public static int Next(int value) => value + 2;\n"
        "}\n",
        encoding="utf-8",
    )
    _git(root, "add", "Counter.cs")
    _git(root, "commit", "-m", "introduce synthetic regression")
    return base, _git(root, "rev-parse", "HEAD")


def _wait_coding(manager: CodingManager, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 1_800
    while time.monotonic() < deadline:
        status = manager.status(CodingStatusCommand(job_id=job_id, limit=40_000))
        if status.state in {CodingJobState.SUCCEEDED, CodingJobState.FAILED}:
            return status.model_dump(mode="json", exclude={"patch_chunk"})
        time.sleep(2)
    raise TimeoutError(f"coding job {job_id} did not finish")


def _wait_review(manager: CodeReviewManager, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 1_800
    while time.monotonic() < deadline:
        status = manager.status(CodeReviewStatusCommand(job_id=job_id, limit=100))
        if status.state in {CodeReviewJobState.SUCCEEDED, CodeReviewJobState.FAILED}:
            return status.model_dump(mode="json", exclude={"findings"})
        time.sleep(2)
    raise TimeoutError(f"review job {job_id} did not finish")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="perform five billed requests")
    parser.add_argument("--account-uid", required=True)
    parser.add_argument(
        "--models",
        help="optional comma-separated fixed model IDs; defaults to all five",
    )
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required because this smoke makes billed requests")
    account = load_opencode_account()
    if account.uid != args.account_uid:
        parser.error("--account-uid must equal the selected environment account UID")
    all_models = {model.value for model in CodingModel} | {model.value for model in CodeReviewModel}
    selected_models = (
        {value.strip() for value in args.models.split(",") if value.strip()}
        if args.models
        else all_models
    )
    unknown = selected_models - all_models
    if unknown:
        parser.error(f"unsupported smoke models: {', '.join(sorted(unknown))}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = user_data_path("AskAIMCP", appauthor=False) / "opencode-smoke" / stamp
    repository = root / "synthetic-repository"
    base, head = _make_repository(repository)
    catalog = CodeReviewRepositoryCatalog({"smoke": repository})
    usage = UsageStore()

    coding = CodingManager(
        usage_store=usage,
        snapshotter=CodingSnapshotter(catalog),
        account=account,
        state_root=root / "coding",
    )
    coding_results: list[dict[str, object]] = []
    for model in CodingModel:
        if model.value not in selected_models:
            continue
        submission = coding.submit(
            CodingSubmitCommand(
                repository_id="smoke",
                base_ref=head,
                task_kind=CodingTaskKind.BUG_FIX,
                model=model,
                target_files=["Counter.cs"],
                requirements=["Counter.Next must return exactly value plus one."],
                acceptance_tests=["Counter.Next(4) equals 5."],
            )
        )
        coding_results.append(
            {
                "model": model.value,
                "job_id": submission.job_id,
                **_wait_coding(coding, submission.job_id),
            }
        )

    review = CodeReviewManager(
        store=CodeReviewStore(root / "review"),
        usage_store=usage,
        snapshotter=CodeReviewSnapshotter(catalog),
        account=account,
    )
    review_group_id = str(uuid4())
    review_results: list[dict[str, object]] = []
    for model in CodeReviewModel:
        if model.value not in selected_models:
            continue
        submission = review.submit(
            CodeReviewSubmitCommand(
                repository_id="smoke",
                base_ref=base,
                head_ref=head,
                review_profile=CodeReviewProfile.GENERAL,
                model=model,
                review_group_id=review_group_id,
            )
        )
        review_results.append(
            {
                "model": model.value,
                "job_id": submission.job_id,
                **_wait_review(review, submission.job_id),
            }
        )

    usage_summary = usage.summarize(days=30)
    account_ledger = next(
        (
            item.model_dump(mode="json")
            for item in usage_summary.opencode_go_accounts
            if item.account == account.uid
        ),
        None,
    )
    report = {
        "account_uid": account.uid,
        "account_alias": account.alias,
        "created_at": datetime.now(UTC).isoformat(),
        "root": str(root),
        "synthetic_base": base,
        "synthetic_head": head,
        "coding": coding_results,
        "review": review_results,
        "ledger": {
            "total_calls": usage_summary.total_calls,
            "successful_calls": usage_summary.successful_calls,
            "failed_calls": usage_summary.failed_calls,
            "estimated_cost_usd": usage_summary.estimated_cost_usd,
            "estimated_cost_usd_by_provider_model": (
                usage_summary.estimated_cost_usd_by_provider_model
            ),
            "account": account_ledger,
        },
    }
    report_path = root / "smoke-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return (
        0 if all(item["state"] == "succeeded" for item in [*coding_results, *review_results]) else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
