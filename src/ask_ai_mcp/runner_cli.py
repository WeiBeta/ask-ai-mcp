"""Local status command for the disabled-by-default execution backend."""

from __future__ import annotations

import argparse

from ask_ai_mcp.sandbox import docker_backend_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ask-ai-mcp-runner")
    parser.add_subparsers(dest="command", required=True).add_parser("status")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    status = docker_backend_status()
    print(status.model_dump_json(indent=2))
    return 0 if status.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
