"""Operator-only reconciliation for a billed Review transport failure."""

from __future__ import annotations

import argparse
from datetime import datetime

from ask_ai_mcp.code_review import CodeReviewManager
from ask_ai_mcp.code_review_models import CodeReviewExternalUsageCommand


def parser() -> argparse.ArgumentParser:
    selected = argparse.ArgumentParser(
        description="Reconcile one externally observed OpenCode Go Review charge"
    )
    selected.add_argument("--job-id", required=True)
    selected.add_argument("--observed-at", required=True, help="timezone-aware ISO-8601 timestamp")
    selected.add_argument("--input-tokens", required=True, type=int)
    selected.add_argument("--output-tokens", required=True, type=int)
    selected.add_argument("--cost-usd", required=True, type=float)
    return selected


def main() -> None:
    args = parser().parse_args()
    command = CodeReviewExternalUsageCommand(
        job_id=args.job_id,
        observed_at=datetime.fromisoformat(args.observed_at),
        input_tokens=args.input_tokens,
        output_tokens=args.output_tokens,
        provider_reported_cost_usd=args.cost_usd,
    )
    receipt = CodeReviewManager().reconcile_external_usage(command)
    print(receipt.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
