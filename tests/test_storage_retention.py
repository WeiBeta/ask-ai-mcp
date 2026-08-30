from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ask_ai_mcp import server, storage_retention
from ask_ai_mcp.models import StorageRetentionMode
from ask_ai_mcp.storage_retention import (
    MIB,
    REPLAY_MAX_BYTES,
    RETENTION_RECEIPT_NAME,
    AtomicBundleRetention,
    StorageDomainPolicy,
    StorageRetentionManager,
    default_storage_policies,
)
from ask_ai_mcp.usage import UsageStore


def test_storage_status_is_content_free_and_counts_regular_files(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.bin").write_bytes(b"a" * 7)
    (second / "b.bin").write_bytes(b"b" * 11)
    manager = StorageRetentionManager(
        (
            StorageDomainPolicy(
                "bounded_test",
                (first, second),
                StorageRetentionMode.ROLLING,
                16,
            ),
        )
    )

    payload = manager.status()

    assert payload.total_bytes == 18
    domain = payload.domains[0]
    assert domain.current_bytes == 18
    assert domain.file_count == 2
    assert domain.root_count == 2
    assert domain.over_limit is True
    assert domain.maintenance_required is True
    assert str(tmp_path) not in payload.model_dump_json()


def test_missing_root_is_empty_without_becoming_an_error(tmp_path: Path) -> None:
    manager = StorageRetentionManager(
        (
            StorageDomainPolicy(
                "missing_test",
                (tmp_path / "missing",),
                StorageRetentionMode.ARCHIVE,
                MIB,
            ),
        )
    )

    domain = manager.status().domains[0]

    assert domain.current_bytes == 0
    assert domain.file_count == 0
    assert domain.protected_entry_count == 0
    assert domain.maintenance_required is False


def test_default_policies_keep_replay_at_exactly_100_mib(tmp_path: Path) -> None:
    policies = {policy.domain_id: policy for policy in default_storage_policies(tmp_path)}

    assert len(policies) == 11
    assert policies["replay_capsules"].limit_bytes == REPLAY_MAX_BYTES == 100 * MIB
    assert policies["replay_capsules"].retention_mode is StorageRetentionMode.ROLLING
    assert policies["usage_database"].retention_mode is StorageRetentionMode.ARCHIVE
    assert policies["review_database"].retention_mode is StorageRetentionMode.ARCHIVE
    assert policies["verified_registry"].retention_mode is StorageRetentionMode.PROTECTED


def test_default_status_policies_follow_configured_module_roots(
    tmp_path: Path, monkeypatch
) -> None:
    base = tmp_path / "base"
    base.mkdir()
    review = tmp_path / "review-custom"
    coding = tmp_path / "coding-custom"
    source = tmp_path / "source-custom"
    replay = tmp_path / "replay-custom"
    patch_one = tmp_path / "patch-one"
    patch_two = tmp_path / "patch-two"
    for root in (review, coding, source, replay, patch_one, patch_two):
        root.mkdir()
    monkeypatch.setattr(storage_retention, "default_state_root", lambda: base)
    monkeypatch.setenv("ASK_AI_MCP_REVIEW_STATE_ROOT", str(review))
    monkeypatch.setenv("ASK_AI_MCP_CODING_STATE_ROOT", str(coding))
    monkeypatch.setenv("ASK_AI_MCP_SOURCE_JOBS_ROOT", str(source))
    monkeypatch.setenv("ASK_AI_MCP_REPLAY_ROOT", str(replay))
    monkeypatch.setenv("ASK_AI_MCP_REVIEW_PATCH_ROOTS", f"{patch_one};{patch_two}")

    policies = {policy.domain_id: policy for policy in default_storage_policies()}

    assert policies["review_jobs"].roots == (review / "jobs",)
    assert policies["coding_jobs"].roots == (coding / "jobs",)
    assert policies["source_jobs"].roots == (source,)
    assert policies["replay_capsules"].roots == (replay,)
    assert policies["review_patch_staging"].roots == (patch_one, patch_two)
    assert policies["review_patch_staging"].limit_bytes == 2 * 1024 * MIB


def test_usage_status_includes_compact_storage_without_new_tool(
    tmp_path: Path, monkeypatch
) -> None:
    manager = StorageRetentionManager(
        (
            StorageDomainPolicy(
                "usage_test",
                (tmp_path / "payload",),
                StorageRetentionMode.ROLLING,
                MIB,
            ),
        )
    )
    monkeypatch.setattr(server, "get_usage_store", lambda: UsageStore(tmp_path / "usage.db"))
    monkeypatch.setattr(server, "get_storage_retention_manager", lambda: manager)

    status = server.usage_status(days=1)

    assert status.storage_retention is not None
    assert status.storage_retention.domains[0].domain_id == "usage_test"
    assert [tool.name for tool in asyncio.run(server.core_mcp.list_tools())].count(
        "usage_status"
    ) == 1


def _bundle_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def test_atomic_bundle_retention_evicts_only_oldest_sealed_bundle(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    older = root / "older"
    newer = root / "newer"
    older.mkdir(parents=True)
    newer.mkdir()
    (older / "payload.bin").write_bytes(b"a" * 100)
    (newer / "payload.bin").write_bytes(b"b" * 100)
    sealing = AtomicBundleRetention(root=root, domain_id="test_jobs", limit_bytes=MIB)
    now = datetime(2026, 8, 30, tzinfo=UTC)
    sealing.seal("older", terminal_at=now)
    sealing.seal("newer", terminal_at=now + timedelta(seconds=1))
    limit = _bundle_size(newer)

    rolling = AtomicBundleRetention(root=root, domain_id="test_jobs", limit_bytes=limit)
    result = rolling.maintain(protected_bundle_ids=frozenset({"newer"}))

    assert result.evicted_bundle_count == 1
    assert result.over_limit is False
    assert not older.exists()
    assert (newer / RETENTION_RECEIPT_NAME).is_file()
    assert not list(root.glob(".*.retention-evicted"))


def test_unsealed_or_modified_bundle_remains_protected(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    unsealed = root / "unsealed"
    changed = root / "changed"
    unsealed.mkdir(parents=True)
    changed.mkdir()
    (unsealed / "payload.bin").write_bytes(b"a" * 100)
    (changed / "payload.bin").write_bytes(b"b" * 100)
    retention = AtomicBundleRetention(root=root, domain_id="test_jobs", limit_bytes=1)
    retention.seal("changed", terminal_at=datetime(2026, 8, 30, tzinfo=UTC))
    (changed / "payload.bin").write_bytes(b"modified")

    result = retention.maintain()

    assert result.evicted_bundle_count == 0
    assert result.over_limit is True
    assert unsealed.is_dir()
    assert changed.is_dir()


def test_declared_ephemeral_subtree_may_change_without_invalidating_bundle(
    tmp_path: Path,
) -> None:
    root = tmp_path / "jobs"
    bundle = root / "terminal"
    (bundle / "wire").mkdir(parents=True)
    (bundle / "job.json").write_text('{"state":"succeeded"}', encoding="utf-8")
    (bundle / "wire" / "response.bin").write_bytes(b"first")
    retention = AtomicBundleRetention(
        root=root,
        domain_id="coding_jobs",
        limit_bytes=1,
        excluded_paths=("wire",),
    )
    retention.seal("terminal", terminal_at=datetime(2026, 8, 30, tzinfo=UTC))
    (bundle / "wire" / "response.bin").write_bytes(b"changed after export")

    result = retention.maintain()

    assert result.evicted_bundle_count == 1
    assert result.over_limit is False
    assert not bundle.exists()


def test_retention_receipt_is_content_free_and_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    bundle = root / "terminal"
    bundle.mkdir(parents=True)
    secret_text = "synthetic-content-that-must-not-enter-the-receipt"
    (bundle / "payload.txt").write_text(secret_text, encoding="utf-8")
    retention = AtomicBundleRetention(root=root, domain_id="test_jobs", limit_bytes=MIB)
    terminal_at = datetime(2026, 8, 30, tzinfo=UTC)

    first = retention.seal("terminal", terminal_at=terminal_at)
    second = retention.seal("terminal", terminal_at=terminal_at)

    receipt = first.read_text(encoding="utf-8")
    assert first == second
    assert secret_text not in receipt
    assert str(tmp_path) not in receipt
