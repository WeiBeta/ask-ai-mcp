from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ask_ai_mcp.models import ToolBuildSpec, ToolCategory
from ask_ai_mcp.replay import (
    REPLAY_RETAIN_MARKER,
    ReplayCaptureError,
    ReplayStore,
    ToolsmithReplayCapsule,
)

BUDGET_SESSION_ID = "11111111-1111-4111-8111-111111111111"
CAPTURED_AT = datetime(2026, 8, 30, tzinfo=UTC)


def make_capsule(
    lifecycle_id: str,
    *,
    captured_at: datetime = CAPTURED_AT,
    padding: str = "",
) -> ToolsmithReplayCapsule:
    return ToolsmithReplayCapsule(
        lifecycle_id=lifecycle_id,
        budget_session_id=BUDGET_SESSION_ID,
        client_name="retention_test",
        captured_at=captured_at,
        prompt_template_version="retention-test-v1",
        prompt_template_sha256="0" * 64,
        spec=ToolBuildSpec(
            name="retain_fixture",
            category=ToolCategory.TEST_UTILITY,
            purpose="Exercise deterministic replay retention without a provider call.",
            input_contract="Synthetic replay input with no external files or secrets.",
            output_contract="One immutable replay capsule and its digest sidecar.",
            acceptance_tests=["The capsule remains hash-valid after rolling retention."],
            fixture_notes=padding,
        ),
    )


def capsule_size(root: Path, lifecycle_id: str) -> int:
    capsule_root = root / lifecycle_id
    return sum(path.stat().st_size for path in capsule_root.iterdir() if path.is_file())


def test_replay_write_publishes_only_a_complete_capsule_directory(tmp_path: Path) -> None:
    lifecycle_id = "00000000-0000-4000-8000-000000000001"
    root = tmp_path / "replay"
    store = ReplayStore(root, max_bytes=1024 * 1024)

    target = store.write(make_capsule(lifecycle_id))

    assert target == root / lifecycle_id / "capsule.json"
    assert store.load(lifecycle_id).lifecycle_id == lifecycle_id
    assert {path.name for path in root.iterdir()} == {lifecycle_id}
    assert {path.name for path in target.parent.iterdir()} == {
        "capsule.json",
        "capsule.sha256",
    }


def test_replay_rolls_oldest_complete_capsule_as_one_unit(tmp_path: Path) -> None:
    first_id = "00000000-0000-4000-8000-000000000001"
    second_id = "00000000-0000-4000-8000-000000000002"
    root = tmp_path / "replay"
    seed = ReplayStore(root, max_bytes=1024 * 1024)
    seed.write(make_capsule(first_id))
    limit = capsule_size(root, first_id)

    rolling = ReplayStore(root, max_bytes=limit)
    rolling.write(make_capsule(second_id))

    assert not (root / first_id).exists()
    assert rolling.load(second_id).lifecycle_id == second_id
    assert not list(root.glob(".*.replay-tmp"))
    assert not list(root.glob(".*.replay-evicted"))


def test_replay_roll_tie_breaks_by_lifecycle_id(tmp_path: Path) -> None:
    first_id = "00000000-0000-4000-8000-000000000001"
    second_id = "00000000-0000-4000-8000-000000000002"
    third_id = "00000000-0000-4000-8000-000000000003"
    root = tmp_path / "replay"
    seed = ReplayStore(root, max_bytes=1024 * 1024)
    seed.write(make_capsule(second_id))
    seed.write(make_capsule(first_id))
    single_size = capsule_size(root, first_id)

    rolling = ReplayStore(root, max_bytes=single_size * 2)
    rolling.write(make_capsule(third_id))

    assert not (root / first_id).exists()
    assert (root / second_id).is_dir()
    assert (root / third_id).is_dir()


def test_replay_retained_capsule_blocks_eviction_and_preserves_new_failure(
    tmp_path: Path,
) -> None:
    retained_id = "00000000-0000-4000-8000-000000000001"
    new_id = "00000000-0000-4000-8000-000000000002"
    root = tmp_path / "replay"
    seed = ReplayStore(root, max_bytes=1024 * 1024)
    seed.write(make_capsule(retained_id))
    seed.retain(retained_id)
    limit = capsule_size(root, retained_id)

    rolling = ReplayStore(root, max_bytes=limit)
    with pytest.raises(ReplayCaptureError, match="protected replay data"):
        rolling.write(make_capsule(new_id))

    assert (root / retained_id / REPLAY_RETAIN_MARKER).is_file()
    assert rolling.load(retained_id).lifecycle_id == retained_id
    assert not (root / new_id).exists()


def test_tampered_capsule_is_protected_instead_of_silently_evicted(tmp_path: Path) -> None:
    tampered_id = "00000000-0000-4000-8000-000000000001"
    new_id = "00000000-0000-4000-8000-000000000002"
    root = tmp_path / "replay"
    seed = ReplayStore(root, max_bytes=1024 * 1024)
    seed.write(make_capsule(tampered_id))
    limit = capsule_size(root, tampered_id)
    (root / tampered_id / "capsule.json").write_text("{}", encoding="utf-8")

    rolling = ReplayStore(root, max_bytes=limit)
    with pytest.raises(ReplayCaptureError, match="protected replay data"):
        rolling.write(make_capsule(new_id))

    assert (root / tampered_id / "capsule.json").read_text(encoding="utf-8") == "{}"
    assert not (root / new_id).exists()


def test_single_oversized_capsule_fails_before_existing_data_is_removed(
    tmp_path: Path,
) -> None:
    existing_id = "00000000-0000-4000-8000-000000000001"
    oversized_id = "00000000-0000-4000-8000-000000000002"
    root = tmp_path / "replay"
    seed = ReplayStore(root, max_bytes=1024 * 1024)
    seed.write(make_capsule(existing_id))
    limit = capsule_size(root, existing_id)

    rolling = ReplayStore(root, max_bytes=limit)
    with pytest.raises(ReplayCaptureError, match="exceeds"):
        rolling.write(make_capsule(oversized_id, padding="x" * 4000))

    assert rolling.load(existing_id).lifecycle_id == existing_id
    assert not (root / oversized_id).exists()


def test_identical_replay_write_remains_idempotent_at_quota(tmp_path: Path) -> None:
    lifecycle_id = "00000000-0000-4000-8000-000000000001"
    capsule = make_capsule(lifecycle_id)
    root = tmp_path / "replay"
    seed = ReplayStore(root, max_bytes=1024 * 1024)
    first = seed.write(capsule)
    limit = capsule_size(root, lifecycle_id)

    second = ReplayStore(root, max_bytes=limit).write(capsule)

    assert second == first
    assert {path.name for path in root.iterdir()} == {lifecycle_id}


def test_replay_restores_all_old_capsules_when_eviction_prepare_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_id = "00000000-0000-4000-8000-000000000001"
    second_id = "00000000-0000-4000-8000-000000000002"
    new_id = "00000000-0000-4000-8000-000000000003"
    root = tmp_path / "replay"
    seed = ReplayStore(root, max_bytes=1024 * 1024)
    seed.write(make_capsule(first_id))
    seed.write(make_capsule(second_id))
    limit = capsule_size(root, first_id)
    real_replace = os.replace
    eviction_renames = 0

    def fail_second_eviction(source: str | Path, destination: str | Path) -> None:
        nonlocal eviction_renames
        if str(destination).endswith(".replay-evicted"):
            eviction_renames += 1
            if eviction_renames == 2:
                raise PermissionError("simulated second eviction denial")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_eviction)
    with pytest.raises(ReplayCaptureError, match="failed to publish"):
        ReplayStore(root, max_bytes=limit).write(make_capsule(new_id))

    assert ReplayStore(root, max_bytes=1024 * 1024).load(first_id).lifecycle_id == first_id
    assert ReplayStore(root, max_bytes=1024 * 1024).load(second_id).lifecycle_id == second_id
    assert not (root / new_id).exists()
    assert not list(root.glob(".*.replay-evicted"))
