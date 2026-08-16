"""Safe asynchronous source-extraction boundary awaiting a concrete local Qwen backend."""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import shutil
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol
from uuid import UUID, uuid4

from platformdirs import user_data_path

from ask_ai_mcp.models import (
    CanonicalEvidenceBundle,
    ModelProvider,
    SourceBackendStatus,
    SourceExtractionCommand,
    SourceExtractionProfile,
    SourceJobReport,
    SourceJobState,
    SourceJobSubmission,
    SourceOutputArtifact,
)

SOURCE_INPUT_ROOTS_ENV = "ASK_AI_MCP_SOURCE_INPUT_ROOTS"
SOURCE_JOBS_ROOT_ENV = "ASK_AI_MCP_SOURCE_JOBS_ROOT"
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 16 * 1024 * 1024 * 1024
MAX_OUTPUT_FILES = 200
MAX_TOTAL_OUTPUT_BYTES = 500 * 1024 * 1024

_DOCUMENT_SUFFIXES = frozenset({".pdf", ".pptx", ".txt", ".md", ".csv", ".json"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"})
_LOGGER = logging.getLogger(__name__)


class SourceProcessingError(RuntimeError):
    """Safe error for an invalid or unavailable source-processing request."""


@dataclass(frozen=True, slots=True)
class StagedSource:
    original_name: str
    staged_name: str
    path: Path
    sha256: str
    size_bytes: int
    media_type: str


@dataclass(frozen=True, slots=True)
class SourceBackendResult:
    warnings: tuple[str, ...] = ()


class SourceBackend(Protocol):
    def status(self) -> SourceBackendStatus: ...

    def extract(
        self,
        command: SourceExtractionCommand,
        staged_sources: list[StagedSource],
        output_directory: Path,
    ) -> SourceBackendResult: ...


class UnconfiguredQwenBackend:
    """Explicit stop point: protocol exists, but no runtime assumptions are fabricated."""

    def status(self) -> SourceBackendStatus:
        return SourceBackendStatus(
            provider=ModelProvider.LOCAL_QWEN,
            configured=False,
            ready=False,
            supported_profiles=list(SourceExtractionProfile),
            detail="Local Qwen runtime is not configured yet.",
        )

    def extract(
        self,
        command: SourceExtractionCommand,
        staged_sources: list[StagedSource],
        output_directory: Path,
    ) -> SourceBackendResult:
        raise SourceProcessingError("local Qwen runtime is not configured")


def default_source_jobs_root() -> Path:
    configured = os.environ.get(SOURCE_JOBS_ROOT_ENV, "").strip()
    if configured:
        return Path(configured)
    return user_data_path("AskAIMCP", appauthor=False, ensure_exists=True) / "source-jobs"


def load_source_input_roots() -> list[Path]:
    raw = os.environ.get(SOURCE_INPUT_ROOTS_ENV, "")
    return [Path(value.strip()) for value in raw.split(";") if value.strip()]


class SourceJobManager:
    """Stage read-only copies and serialize one local GPU extraction job at a time."""

    def __init__(
        self,
        *,
        backend: SourceBackend | None = None,
        jobs_root: Path | None = None,
        allowed_input_roots: list[Path] | None = None,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.backend = backend or UnconfiguredQwenBackend()
        self.jobs_root = (jobs_root or default_source_jobs_root()).resolve()
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        roots = (
            allowed_input_roots if allowed_input_roots is not None else load_source_input_roots()
        )
        self.allowed_input_roots = [self._validate_root(root) for root in roots]
        self.executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="ask-ai-source",
        )
        self._reports: dict[str, SourceJobReport] = {}
        self._lock = Lock()

    def backend_status(self) -> SourceBackendStatus:
        status = self.backend.status()
        active = sum(
            report.state in {SourceJobState.QUEUED, SourceJobState.RUNNING}
            for report in self._snapshot_reports()
        )
        if not self.allowed_input_roots:
            return status.model_copy(
                update={
                    "ready": False,
                    "active_jobs": active,
                    "detail": "Source input roots are not configured.",
                }
            )
        return status.model_copy(update={"active_jobs": active})

    def submit(self, command: SourceExtractionCommand) -> SourceJobSubmission:
        status = self.backend_status()
        if not status.ready:
            raise SourceProcessingError(status.detail)
        resolved_sources = self._validate_sources(command)
        job_id = str(uuid4())
        job_root = self._within_jobs_root(self.jobs_root / job_id)
        (job_root / "control").mkdir(parents=True)
        (job_root / "input").mkdir()
        (job_root / "output").mkdir()
        report = SourceJobReport(
            job_id=job_id,
            state=SourceJobState.QUEUED,
            profile=command.profile,
            progress_percent=0,
            detail="Source extraction is queued.",
        )
        self._set_report(report)
        self.executor.submit(self._run, job_root, command, resolved_sources)
        return SourceJobSubmission(
            job_id=job_id,
            profile=command.profile,
            source_count=len(command.source_files),
        )

    def job_status(self, job_id: str) -> SourceJobReport:
        try:
            UUID(job_id)
        except ValueError as error:
            raise SourceProcessingError("invalid source job identifier") from error
        with self._lock:
            report = self._reports.get(job_id)
        if report is not None:
            return report
        path = self._within_jobs_root(self.jobs_root / job_id / "control" / "report.json")
        if not path.is_file():
            raise SourceProcessingError("source job was not found")
        return SourceJobReport.model_validate_json(path.read_text(encoding="utf-8"))

    def _run(
        self,
        job_root: Path,
        command: SourceExtractionCommand,
        resolved_sources: list[Path],
    ) -> None:
        job_id = job_root.name
        try:
            self._set_report(
                SourceJobReport(
                    job_id=job_id,
                    state=SourceJobState.RUNNING,
                    profile=command.profile,
                    progress_percent=10,
                    detail="Staging immutable source copies.",
                )
            )
            staged = self._stage_sources(resolved_sources, job_root / "input")
            result = self.backend.extract(command, staged, job_root / "output")
            self._validate_evidence_bundle(job_root / "output", command, staged)
            backend_outputs = self._collect_outputs(job_root / "output")
            self._write_manifest(job_root, command, staged, backend_outputs, result)
            outputs = self._collect_outputs(job_root / "output")
            self._set_report(
                SourceJobReport(
                    job_id=job_id,
                    state=SourceJobState.SUCCEEDED,
                    profile=command.profile,
                    progress_percent=100,
                    detail="Source extraction completed.",
                    output_directory=str(job_root / "output"),
                    artifacts=outputs,
                    warnings=list(result.warnings),
                )
            )
        except Exception as error:
            _LOGGER.warning("source extraction failed: %s", type(error).__name__)
            self._set_report(
                SourceJobReport(
                    job_id=job_id,
                    state=SourceJobState.FAILED,
                    profile=command.profile,
                    progress_percent=100,
                    detail="Source extraction failed; inspect local application logs.",
                    failure_kind=type(error).__name__,
                )
            )

    def _validate_sources(self, command: SourceExtractionCommand) -> list[Path]:
        allowed_suffixes = _DOCUMENT_SUFFIXES | _IMAGE_SUFFIXES
        resolved_sources = []
        total_size = 0
        for raw_path in command.source_files:
            path = Path(raw_path)
            if not path.is_absolute():
                raise SourceProcessingError("source paths must be absolute")
            resolved = path.resolve(strict=True)
            if not resolved.is_file() or self._is_link(path):
                raise SourceProcessingError("source path is not a plain file")
            if not any(resolved.is_relative_to(root) for root in self.allowed_input_roots):
                raise SourceProcessingError("source file is outside configured input roots")
            if resolved.suffix.casefold() not in allowed_suffixes:
                raise SourceProcessingError("source type is not supported for the selected profile")
            size = resolved.stat().st_size
            if size > MAX_SOURCE_FILE_BYTES:
                raise SourceProcessingError("one source file exceeds the 8 GiB limit")
            total_size += size
            if total_size > MAX_TOTAL_SOURCE_BYTES:
                raise SourceProcessingError("total source size exceeds the 16 GiB limit")
            resolved_sources.append(resolved)
        return resolved_sources

    def _stage_sources(self, sources: list[Path], destination_root: Path) -> list[StagedSource]:
        staged = []
        for index, source in enumerate(sources, start=1):
            suffix = source.suffix.casefold()
            staged_name = f"source-{index:04d}{suffix}"
            destination = destination_root / staged_name
            before_hash = self._hash_file(source)
            shutil.copyfile(source, destination)
            staged_hash = self._hash_file(destination)
            after_hash = self._hash_file(source)
            if before_hash != staged_hash or before_hash != after_hash:
                raise SourceProcessingError("source changed while its copy was staged")
            media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            staged.append(
                StagedSource(
                    original_name=source.name,
                    staged_name=staged_name,
                    path=destination,
                    sha256=staged_hash,
                    size_bytes=destination.stat().st_size,
                    media_type=media_type,
                )
            )
        return staged

    def _write_manifest(
        self,
        job_root: Path,
        command: SourceExtractionCommand,
        staged: list[StagedSource],
        outputs: list[SourceOutputArtifact],
        result: SourceBackendResult,
    ) -> None:
        status = self.backend.status()
        manifest = {
            "contract": "canonical_evidence_v1",
            "job_id": job_root.name,
            "created_at": datetime.now(UTC).isoformat(),
            "profile": command.profile.value,
            "detail_level": command.detail_level.value,
            "selection": {
                "page_start": command.page_start,
                "page_end": command.page_end,
                "language_hint": command.language_hint,
            },
            "backend": {
                "provider": status.provider.value,
                "model_id": status.model_id,
                "runtime": status.runtime,
            },
            "inputs": [
                {
                    "original_name": item.original_name,
                    "staged_name": item.staged_name,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                    "media_type": item.media_type,
                }
                for item in staged
            ],
            "outputs": [item.model_dump(mode="json") for item in outputs],
            "warnings": list(result.warnings),
        }
        (job_root / "output" / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _validate_evidence_bundle(
        self,
        output_root: Path,
        command: SourceExtractionCommand,
        staged: list[StagedSource],
    ) -> CanonicalEvidenceBundle:
        path = output_root / "evidence.json"
        if not path.is_file() or self._is_link(path):
            raise SourceProcessingError("source backend did not produce evidence.json")
        if path.stat().st_size > MAX_TOTAL_OUTPUT_BYTES:
            raise SourceProcessingError("evidence output exceeds the 500 MiB limit")
        bundle = CanonicalEvidenceBundle.model_validate_json(path.read_text(encoding="utf-8"))
        if bundle.profile is not command.profile:
            raise SourceProcessingError("evidence profile does not match the request")
        allowed_hashes = {item.sha256 for item in staged}
        if any(record.source_sha256 not in allowed_hashes for record in bundle.records):
            raise SourceProcessingError("evidence refers to an unknown source hash")
        return bundle

    def _collect_outputs(self, root: Path) -> list[SourceOutputArtifact]:
        artifacts = []
        total_size = 0
        for path in sorted(root.rglob("*")):
            if self._is_link(path):
                raise SourceProcessingError("output tree contains a link")
            if not path.is_file():
                continue
            if len(artifacts) >= MAX_OUTPUT_FILES:
                raise SourceProcessingError("output file count exceeds 200")
            size = path.stat().st_size
            total_size += size
            if total_size > MAX_TOTAL_OUTPUT_BYTES:
                raise SourceProcessingError("total output size exceeds 500 MiB")
            relative = path.relative_to(root).as_posix()
            artifacts.append(
                SourceOutputArtifact(
                    relative_path=relative,
                    sha256=self._hash_file(path),
                    size_bytes=size,
                    media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                )
            )
        return artifacts

    def _set_report(self, report: SourceJobReport) -> None:
        with self._lock:
            self._reports[report.job_id] = report
        control_root = self._within_jobs_root(self.jobs_root / report.job_id / "control")
        target = control_root / "report.json"
        temporary = control_root / f".{uuid4()}.tmp"
        temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)

    def _snapshot_reports(self) -> list[SourceJobReport]:
        with self._lock:
            return list(self._reports.values())

    def _validate_root(self, root: Path) -> Path:
        resolved = root.resolve(strict=True)
        if not resolved.is_dir() or self._is_link(root):
            raise SourceProcessingError("configured source root is not a plain directory")
        return resolved

    def _within_jobs_root(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.jobs_root):
            raise SourceProcessingError("source job path escapes configured root")
        return resolved

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _is_link(path: Path) -> bool:
        metadata = path.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        return path.is_symlink() or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
