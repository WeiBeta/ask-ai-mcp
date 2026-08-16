"""Administrative CLI for explicit replay-corpus maintenance."""

from __future__ import annotations

import argparse
from pathlib import Path

from platformdirs import user_data_path

from ask_ai_mcp.replay import LegacyReplayImporter, ReplayStore
from ask_ai_mcp.shadow import QwenShadowEvaluator


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Maintain local Ask AI replay capsules")
    command.add_argument("action", choices=["import-existing", "shadow-qwen"])
    command.add_argument("--lifecycle-id")
    command.add_argument("--jobs-root", type=Path)
    command.add_argument("--usage-database", type=Path)
    command.add_argument("--replay-root", type=Path)
    return command


def main() -> None:
    args = parser().parse_args()
    data_root = user_data_path("AskAIMCP", appauthor=False, ensure_exists=True)
    replay_store = ReplayStore(args.replay_root)
    if args.action == "shadow-qwen":
        if not args.lifecycle_id:
            raise SystemExit("--lifecycle-id is required for shadow-qwen")
        comparison, path = QwenShadowEvaluator(replay_store=replay_store).evaluate(
            args.lifecycle_id
        )
        print(
            comparison.model_dump_json(indent=2),
            f"\ncomparison_path={path}",
        )
        return
    importer = LegacyReplayImporter(
        jobs_root=args.jobs_root,
        usage_database=args.usage_database or data_root / "usage.db",
        replay_store=replay_store,
    )
    summary = importer.import_all()
    print(summary.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
