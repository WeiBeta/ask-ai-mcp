"""Explicitly charged, synthetic DeepSeek Flash smoke test."""

from __future__ import annotations

import argparse

from ask_ai_mcp.credentials import CredentialError
from ask_ai_mcp.deepseek import DeepSeekClient, DeepSeekClientError
from ask_ai_mcp.models import ToolBuildSpec, ToolCategory
from ask_ai_mcp.policy import PolicyViolation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ask-ai-mcp-smoke",
        description="Run one synthetic DeepSeek Flash tool-candidate request.",
    )
    parser.add_argument(
        "--confirm-charge",
        action="store_true",
        help="confirm that one prepaid API request may be made",
    )
    return parser


def smoke_spec() -> ToolBuildSpec:
    return ToolBuildSpec(
        name="split_synthetic_fields",
        entrypoint="tool.py",
        category=ToolCategory.TEST_UTILITY,
        purpose="Create a pure Python helper for a synthetic delimiter parsing fixture.",
        input_contract="A synthetic UTF-8 string containing pipe-delimited test fields.",
        output_contract="A JSON-compatible list of stripped non-empty field strings.",
        acceptance_tests=[
            "Input 'alpha | beta | gamma' returns ['alpha', 'beta', 'gamma'].",
            "The implementation performs no file, process, environment, or network access.",
        ],
        prohibited_capabilities=[
            "filesystem access",
            "network access",
            "subprocess execution",
            "environment-variable access",
        ],
        fixture_notes=(
            "The entrypoint must define run(request, input_dir, output_dir); request contains "
            "a synthetic text field and the result is a JSON-compatible list."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_charge:
        print("No request made. Re-run with --confirm-charge to permit one prepaid API call.")
        return 2

    try:
        result = DeepSeekClient().build_candidate(smoke_spec(), client_name="phase1_smoke")
    except (CredentialError, DeepSeekClientError, PolicyViolation) as error:
        print(f"Smoke request failed safely: {error}")
        return 1

    paths = ", ".join(file.path for file in result.payload.files)
    print(f"Smoke request passed with {result.model.value}.")
    print(f"Candidate SHA-256: {result.candidate_sha256}")
    print(f"Candidate files (not written): {paths}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
