"""Keep physical-host operator snapshots out of source control and packages."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_local_operator_archive_is_ignored_as_a_directory() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/operator-archive/local-only/" in gitignore


def test_versioned_operator_docs_do_not_contain_generated_machine_snapshot() -> None:
    versioned = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "operator-archive").rglob("*")
        if path.is_file() and "local-only" not in path.parts
    }
    assert versioned == {
        "operator-archive/MODULE_REFERENCE_ZH-CN.md",
        "operator-archive/README.md",
        "operator-archive/scripts/Update-LocalSnapshot.ps1",
    }
