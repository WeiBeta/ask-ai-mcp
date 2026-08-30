"""Provider-neutral local storage quotas with content-free status reporting."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from platformdirs import user_data_path

from ask_ai_mcp.models import (
    StorageDomainStatus,
    StorageRetentionMode,
    StorageRetentionStatus,
)

STORAGE_POLICY_VERSION = "ask-ai-storage-v1"
RETENTION_RECEIPT_NAME = ".retention.json"
MIB = 1024 * 1024
GIB = 1024 * MIB
REPLAY_MAX_BYTES = 100 * MIB
REVIEW_JOBS_MAX_BYTES = 2 * GIB
CODING_JOBS_MAX_BYTES = GIB
TOOLSMITH_JOBS_MAX_BYTES = GIB
SOURCE_JOBS_MAX_BYTES = 20 * GIB
VERIFIED_RUNS_MAX_BYTES = 2 * GIB
SMOKE_MAX_BYTES = 512 * MIB
PATCH_STAGING_MAX_BYTES = GIB
_RETENTION_LOCK = threading.RLock()
_PATCH_ROOTS_ENV = "ASK_AI_MCP_REVIEW_PATCH_ROOTS"


@dataclass(frozen=True, slots=True)
class StorageDomainPolicy:
    """Internal roots and limits for one public path-free status row."""

    domain_id: str
    roots: tuple[Path, ...]
    retention_mode: StorageRetentionMode
    limit_bytes: int


@dataclass(frozen=True, slots=True)
class _TreeUsage:
    current_bytes: int = 0
    file_count: int = 0
    protected_entry_count: int = 0

    def __add__(self, other: _TreeUsage) -> _TreeUsage:
        return _TreeUsage(
            current_bytes=self.current_bytes + other.current_bytes,
            file_count=self.file_count + other.file_count,
            protected_entry_count=(self.protected_entry_count + other.protected_entry_count),
        )


@dataclass(frozen=True, slots=True)
class RetentionMaintenanceResult:
    current_bytes: int
    evicted_bundle_count: int
    protected_entry_count: int
    over_limit: bool


@dataclass(frozen=True, slots=True)
class _AtomicBundleCandidate:
    bundle_id: str
    root: Path
    terminal_at: float
    size_bytes: int


def default_state_root() -> Path:
    return user_data_path("AskAIMCP", appauthor=False, ensure_exists=True)


def _configured_root(environment_name: str, fallback: Path) -> Path:
    configured = os.environ.get(environment_name, "").strip()
    if not configured:
        return fallback
    selected = Path(configured)
    return selected if selected.is_absolute() else fallback.parent / ".invalid-configured-root"


def default_storage_policies(state_root: Path | None = None) -> tuple[StorageDomainPolicy, ...]:
    root = (state_root or default_state_root()).resolve()
    review_root = (
        root / "code-review"
        if state_root is not None
        else _configured_root("ASK_AI_MCP_REVIEW_STATE_ROOT", root / "code-review")
    )
    coding_root = (
        root / "coding"
        if state_root is not None
        else _configured_root("ASK_AI_MCP_CODING_STATE_ROOT", root / "coding")
    )
    source_root = (
        root / "source-jobs"
        if state_root is not None
        else _configured_root("ASK_AI_MCP_SOURCE_JOBS_ROOT", root / "source-jobs")
    )
    replay_root = (
        root / "replay"
        if state_root is not None
        else _configured_root("ASK_AI_MCP_REPLAY_ROOT", root / "replay")
    )
    patch_roots = tuple(
        Path(value.strip())
        for value in os.environ.get(_PATCH_ROOTS_ENV, "").split(";")
        if value.strip()
    )
    return (
        StorageDomainPolicy(
            "usage_database",
            (root / "usage.db",),
            StorageRetentionMode.ARCHIVE,
            512 * MIB,
        ),
        StorageDomainPolicy(
            "review_database",
            (review_root / "review.db",),
            StorageRetentionMode.ARCHIVE,
            512 * MIB,
        ),
        StorageDomainPolicy(
            "review_jobs",
            (review_root / "jobs",),
            StorageRetentionMode.ROLLING,
            REVIEW_JOBS_MAX_BYTES,
        ),
        StorageDomainPolicy(
            "coding_jobs",
            (coding_root / "jobs",),
            StorageRetentionMode.ROLLING,
            CODING_JOBS_MAX_BYTES,
        ),
        StorageDomainPolicy(
            "toolsmith_jobs",
            (root / "jobs",),
            StorageRetentionMode.ROLLING,
            TOOLSMITH_JOBS_MAX_BYTES,
        ),
        StorageDomainPolicy(
            "source_jobs",
            (source_root,),
            StorageRetentionMode.ROLLING,
            SOURCE_JOBS_MAX_BYTES,
        ),
        StorageDomainPolicy(
            "verified_runs",
            (root / "runs",),
            StorageRetentionMode.ROLLING,
            VERIFIED_RUNS_MAX_BYTES,
        ),
        StorageDomainPolicy(
            "replay_capsules",
            (replay_root,),
            StorageRetentionMode.ROLLING,
            REPLAY_MAX_BYTES,
        ),
        StorageDomainPolicy(
            "opencode_smoke",
            (root / "opencode-smoke",),
            StorageRetentionMode.ROLLING,
            SMOKE_MAX_BYTES,
        ),
        StorageDomainPolicy(
            "verified_registry",
            (root / "registry",),
            StorageRetentionMode.PROTECTED,
            2 * GIB,
        ),
        StorageDomainPolicy(
            "review_patch_staging",
            patch_roots or (root / "unconfigured-review-patch-root",),
            StorageRetentionMode.ROLLING,
            PATCH_STAGING_MAX_BYTES * max(1, len(patch_roots)),
        ),
    )


class StorageRetentionManager:
    """Read content-free usage now; mutation is added only by explicit adapters."""

    def __init__(self, policies: tuple[StorageDomainPolicy, ...] | None = None) -> None:
        self.policies = policies or default_storage_policies()

    def status(self) -> StorageRetentionStatus:
        domains: list[StorageDomainStatus] = []
        total_bytes = 0
        for policy in self.policies:
            usage = _TreeUsage()
            for root in policy.roots:
                usage += _safe_usage(root)
            total_bytes += usage.current_bytes
            over_limit = usage.current_bytes > policy.limit_bytes
            domains.append(
                StorageDomainStatus(
                    domain_id=policy.domain_id,
                    retention_mode=policy.retention_mode,
                    current_bytes=usage.current_bytes,
                    limit_bytes=policy.limit_bytes,
                    file_count=usage.file_count,
                    root_count=len(policy.roots),
                    protected_entry_count=usage.protected_entry_count,
                    over_limit=over_limit,
                    maintenance_required=(over_limit or usage.protected_entry_count > 0),
                )
            )
        return StorageRetentionStatus(
            policy_version=STORAGE_POLICY_VERSION,
            total_bytes=total_bytes,
            domains=domains,
        )


class AtomicBundleRetention:
    """Roll only explicitly sealed, hash-valid child directories as whole units."""

    def __init__(
        self,
        *,
        root: Path,
        domain_id: str,
        limit_bytes: int,
        excluded_paths: tuple[str, ...] = (),
    ) -> None:
        if not domain_id or limit_bytes <= 0:
            raise ValueError("retention domain and limit must be configured")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.domain_id = domain_id
        self.limit_bytes = limit_bytes
        self.excluded_paths = tuple(sorted(set(excluded_paths)))

    def seal(self, bundle_id: str, *, terminal_at: datetime) -> Path:
        """Write one content-free receipt after a domain proves export and terminal state."""

        with _RETENTION_LOCK:
            return self._seal(bundle_id, terminal_at=terminal_at)

    def _seal(self, bundle_id: str, *, terminal_at: datetime) -> Path:
        if terminal_at.tzinfo is None:
            raise ValueError("terminal_at must include a timezone")
        bundle_root = self._bundle_root(bundle_id)
        digest, byte_length, file_count = self._tree_digest(bundle_root)
        receipt = {
            "schema_version": 1,
            "domain_id": self.domain_id,
            "bundle_id": bundle_id,
            "terminal_at": terminal_at.astimezone(UTC).isoformat(),
            "bundle_sha256": digest,
            "byte_length": byte_length,
            "file_count": file_count,
            "excluded_paths": list(self.excluded_paths),
        }
        payload = (
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        target = bundle_root / RETENTION_RECEIPT_NAME
        if target.exists():
            if not target.is_file() or _is_reparse(target.lstat()):
                raise RuntimeError("retention receipt is not a plain file")
            if target.read_bytes() != payload:
                raise RuntimeError("retention receipt conflicts with the sealed bundle")
            return target
        temporary = bundle_root / f".{uuid4()}.retention.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def maintain(
        self,
        *,
        protected_bundle_ids: frozenset[str] = frozenset(),
    ) -> RetentionMaintenanceResult:
        """Evict oldest valid receipts only when the whole root exceeds its cap."""

        with _RETENTION_LOCK:
            return self._maintain(protected_bundle_ids=protected_bundle_ids)

    def _maintain(
        self,
        *,
        protected_bundle_ids: frozenset[str],
    ) -> RetentionMaintenanceResult:
        usage = _safe_usage(self.root)
        if usage.current_bytes <= self.limit_bytes:
            return RetentionMaintenanceResult(
                current_bytes=usage.current_bytes,
                evicted_bundle_count=0,
                protected_entry_count=usage.protected_entry_count,
                over_limit=False,
            )
        candidates: list[_AtomicBundleCandidate] = []
        try:
            children = list(os.scandir(self.root))
        except OSError:
            children = []
        for child in children:
            if child.name in protected_bundle_ids:
                continue
            candidate = self._candidate(Path(child.path))
            if candidate is not None:
                candidates.append(candidate)

        evicted = 0
        for candidate in sorted(
            candidates,
            key=lambda item: (item.terminal_at, item.bundle_id),
        ):
            if usage.current_bytes <= self.limit_bytes:
                break
            current = self._candidate(candidate.root)
            if current != candidate:
                continue
            tombstone = self.root / f".{candidate.bundle_id}.{uuid4()}.retention-evicted"
            try:
                os.replace(candidate.root, tombstone)
                shutil.rmtree(tombstone)
            except OSError:
                try:
                    if tombstone.exists() and not candidate.root.exists():
                        os.replace(tombstone, candidate.root)
                except OSError:
                    pass
                usage = _safe_usage(self.root)
                break
            evicted += 1
            usage = _safe_usage(self.root)
        return RetentionMaintenanceResult(
            current_bytes=usage.current_bytes,
            evicted_bundle_count=evicted,
            protected_entry_count=usage.protected_entry_count,
            over_limit=usage.current_bytes > self.limit_bytes,
        )

    def _candidate(self, bundle_root: Path) -> _AtomicBundleCandidate | None:
        try:
            resolved = bundle_root.resolve(strict=True)
            metadata = bundle_root.lstat()
        except OSError:
            return None
        if (
            resolved.parent != self.root
            or resolved.name != bundle_root.name
            or _is_reparse(metadata)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            return None
        receipt_path = resolved / RETENTION_RECEIPT_NAME
        try:
            receipt_stat = receipt_path.lstat()
            if _is_reparse(receipt_stat) or receipt_stat.st_size > 4096:
                return None
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        expected_keys = {
            "schema_version",
            "domain_id",
            "bundle_id",
            "terminal_at",
            "bundle_sha256",
            "byte_length",
            "file_count",
            "excluded_paths",
        }
        if (
            not isinstance(receipt, dict)
            or set(receipt) != expected_keys
            or receipt.get("schema_version") != 1
            or receipt.get("domain_id") != self.domain_id
            or receipt.get("bundle_id") != resolved.name
            or receipt.get("excluded_paths") != list(self.excluded_paths)
        ):
            return None
        try:
            terminal_at = datetime.fromisoformat(str(receipt["terminal_at"]))
            digest, byte_length, file_count = self._tree_digest(resolved)
        except (OSError, ValueError, RuntimeError):
            return None
        if terminal_at.tzinfo is None or any(
            (
                receipt.get("bundle_sha256") != digest,
                receipt.get("byte_length") != byte_length,
                receipt.get("file_count") != file_count,
            )
        ):
            return None
        usage = _safe_usage(resolved)
        if usage.protected_entry_count:
            return None
        return _AtomicBundleCandidate(
            bundle_id=resolved.name,
            root=resolved,
            terminal_at=terminal_at.astimezone(UTC).timestamp(),
            size_bytes=usage.current_bytes,
        )

    def _bundle_root(self, bundle_id: str) -> Path:
        if (
            not bundle_id
            or bundle_id in {".", ".."}
            or any(character in bundle_id for character in ("/", "\\", ":"))
        ):
            raise ValueError("retention bundle identifier is invalid")
        candidate = self.root / bundle_id
        resolved = candidate.resolve(strict=True)
        metadata = candidate.lstat()
        if (
            resolved.parent != self.root
            or resolved.name != bundle_id
            or not stat.S_ISDIR(metadata.st_mode)
            or _is_reparse(metadata)
        ):
            raise RuntimeError("retention bundle is not a plain direct child")
        return resolved

    def _tree_digest(self, bundle_root: Path) -> tuple[str, int, int]:
        digest = hashlib.sha256()
        byte_length = 0
        file_count = 0
        for directory, directory_names, file_names in os.walk(
            bundle_root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(directory)
            safe_directories: list[str] = []
            for name in sorted(directory_names):
                path = current / name
                if _is_reparse(path.lstat()):
                    raise RuntimeError("retention bundle contains a reparse point")
                relative = path.relative_to(bundle_root).as_posix()
                if not self._excluded(relative):
                    safe_directories.append(name)
            directory_names[:] = safe_directories
            for name in sorted(file_names):
                path = current / name
                relative = path.relative_to(bundle_root).as_posix()
                if relative == RETENTION_RECEIPT_NAME or self._excluded(relative):
                    continue
                metadata = path.lstat()
                if _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
                    raise RuntimeError("retention bundle contains a non-plain file")
                file_digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(MIB):
                        file_digest.update(chunk)
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")
                digest.update(str(metadata.st_size).encode("ascii"))
                digest.update(b"\0")
                digest.update(file_digest.hexdigest().encode("ascii"))
                digest.update(b"\n")
                byte_length += metadata.st_size
                file_count += 1
        return digest.hexdigest(), byte_length, file_count

    def _excluded(self, relative: str) -> bool:
        return any(
            relative == excluded or relative.startswith(excluded.rstrip("/") + "/")
            for excluded in self.excluded_paths
        )


def _safe_usage(root: Path) -> _TreeUsage:
    """Count regular files without following links, junctions or reparse points."""

    try:
        root_stat = root.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return _TreeUsage()
    except OSError:
        return _TreeUsage(protected_entry_count=1)
    if _is_reparse(root_stat):
        return _TreeUsage(protected_entry_count=1)
    if stat.S_ISREG(root_stat.st_mode):
        return _TreeUsage(current_bytes=root_stat.st_size, file_count=1)
    if not stat.S_ISDIR(root_stat.st_mode):
        return _TreeUsage(protected_entry_count=1)

    usage = _TreeUsage()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            usage += _TreeUsage(protected_entry_count=1)
            continue
        for entry in entries:
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                usage += _TreeUsage(protected_entry_count=1)
                continue
            if _is_reparse(entry_stat):
                usage += _TreeUsage(protected_entry_count=1)
            elif stat.S_ISDIR(entry_stat.st_mode):
                pending.append(Path(entry.path))
            elif stat.S_ISREG(entry_stat.st_mode):
                usage += _TreeUsage(current_bytes=entry_stat.st_size, file_count=1)
            else:
                usage += _TreeUsage(protected_entry_count=1)
    return usage


def _is_reparse(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    return stat.S_ISLNK(value.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )
