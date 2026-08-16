"""Tests for the Qwen-independent multimodal source job boundary."""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Event

import pytest

from ask_ai_mcp.models import (
    CanonicalEvidenceBundle,
    ModelProvider,
    SourceBackendStatus,
    SourceExtractionCommand,
    SourceExtractionProfile,
    SourceJobState,
)
from ask_ai_mcp.source import (
    SourceBackendResult,
    SourceJobManager,
    SourceProcessingError,
    StagedSource,
)


class FakeQwenBackend:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.completed = Event()
        self.seen_sources: list[StagedSource] = []

    def status(self) -> SourceBackendStatus:
        return SourceBackendStatus(
            provider=ModelProvider.LOCAL_QWEN,
            configured=True,
            ready=True,
            model_id="Qwen/Qwen3.8-27B-Q4",
            runtime="synthetic-test",
            supported_profiles=list(SourceExtractionProfile),
            detail="Synthetic local backend is ready.",
        )

    def extract(self, command, staged_sources, output_directory) -> SourceBackendResult:
        self.seen_sources = staged_sources
        if self.fail:
            self.completed.set()
            raise RuntimeError("sensitive backend detail")
        (output_directory / "evidence.json").write_text(
            json.dumps(
                {
                    "contract": "canonical_evidence_v1",
                    "profile": command.profile.value,
                    "records": [
                        {
                            "evidence_id": "record-1",
                            "source_sha256": staged_sources[0].sha256,
                            "kind": "text",
                            "location": {"page": 1},
                            "verbatim_text": "synthetic evidence",
                            "extraction_method": "synthetic-test",
                        }
                    ],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        self.completed.set()
        return SourceBackendResult(warnings=("synthetic_fixture",))


def command(path: Path, *, profile=SourceExtractionProfile.DOCUMENT_EVIDENCE):
    return SourceExtractionCommand(source_files=[str(path)], profile=profile)


def wait_for_terminal(manager: SourceJobManager, job_id: str):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        report = manager.job_status(job_id)
        if report.state in {SourceJobState.SUCCEEDED, SourceJobState.FAILED}:
            return report
        time.sleep(0.01)
    raise AssertionError("source job did not reach a terminal state")


def test_unconfigured_backend_and_missing_roots_fail_closed(tmp_path: Path) -> None:
    manager = SourceJobManager(jobs_root=tmp_path / "jobs", allowed_input_roots=[])

    status = manager.backend_status()

    assert status.configured is False
    assert status.ready is False
    assert "roots" in status.detail
    with pytest.raises(SourceProcessingError, match="roots"):
        manager.submit(command(tmp_path / "missing.pdf"))


def test_source_job_stages_copy_and_returns_canonical_manifest(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "复杂 报告.pdf"
    source.write_bytes(b"synthetic-pdf")
    backend = FakeQwenBackend()
    manager = SourceJobManager(
        backend=backend,
        jobs_root=tmp_path / "jobs",
        allowed_input_roots=[source_root],
    )

    submission = manager.submit(command(source))
    assert backend.completed.wait(timeout=2)
    report = wait_for_terminal(manager, submission.job_id)

    assert report.state is SourceJobState.SUCCEEDED
    assert report.progress_percent == 100
    assert report.warnings == ["synthetic_fixture"]
    assert {artifact.relative_path for artifact in report.artifacts} == {
        "evidence.json",
        "manifest.json",
    }
    assert source.read_bytes() == b"synthetic-pdf"
    assert backend.seen_sources[0].path.read_bytes() == b"synthetic-pdf"
    assert backend.seen_sources[0].original_name == "复杂 报告.pdf"
    manifest = json.loads(
        (Path(report.output_directory or "") / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["contract"] == "canonical_evidence_v1"
    assert manifest["backend"]["provider"] == "local_qwen"
    assert manifest["inputs"][0]["original_name"] == "复杂 报告.pdf"
    assert len(manifest["inputs"][0]["sha256"]) == 64


def test_source_rejects_unimplemented_document_type_and_outside_root(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    document = source_root / "report.docx"
    document.write_bytes(b"docx")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    manager = SourceJobManager(
        backend=FakeQwenBackend(),
        jobs_root=tmp_path / "jobs",
        allowed_input_roots=[source_root],
    )

    with pytest.raises(SourceProcessingError, match="type"):
        manager.submit(command(document))
    with pytest.raises(SourceProcessingError, match="outside"):
        manager.submit(command(outside))


def test_backend_failure_returns_kind_without_sensitive_message(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "report.pdf"
    source.write_bytes(b"pdf")
    backend = FakeQwenBackend(fail=True)
    manager = SourceJobManager(
        backend=backend,
        jobs_root=tmp_path / "jobs",
        allowed_input_roots=[source_root],
    )

    submission = manager.submit(command(source))
    assert backend.completed.wait(timeout=2)
    report = wait_for_terminal(manager, submission.job_id)

    assert report.state is SourceJobState.FAILED
    assert report.failure_kind == "RuntimeError"
    assert "sensitive" not in report.detail


def test_canonical_evidence_ids_are_case_insensitively_unique() -> None:
    record = {
        "source_sha256": "a" * 64,
        "kind": "text",
        "location": {"page": 1},
        "verbatim_text": "synthetic evidence",
        "extraction_method": "synthetic-test",
    }

    with pytest.raises(ValueError, match="identifiers must be unique"):
        CanonicalEvidenceBundle.model_validate(
            {
                "profile": "document_evidence",
                "records": [
                    {"evidence_id": "Record-1", **record},
                    {"evidence_id": "record-1", **record},
                ],
            }
        )
