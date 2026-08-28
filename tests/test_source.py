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
    VisualExtractionScope,
)
from ask_ai_mcp.qwen import QwenRequestStillProcessing
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


def test_visual_scope_requires_bounded_focus_ids() -> None:
    base = {
        "source_files": [r"C:\Source\diagram.png"],
        "profile": SourceExtractionProfile.VISUAL_STRUCTURE,
    }

    with pytest.raises(ValueError, match="requires at least one focus_id"):
        SourceExtractionCommand(
            **base,
            visual_scope=VisualExtractionScope.SELECTED_DETAILS,
        )
    with pytest.raises(ValueError, match="requires focus_region_xywh"):
        SourceExtractionCommand(
            **base,
            visual_scope=VisualExtractionScope.SELECTED_DETAILS,
            focus_ids=["08"],
        )
    with pytest.raises(ValueError, match="must fit within the source"):
        SourceExtractionCommand(
            **base,
            visual_scope=VisualExtractionScope.SELECTED_DETAILS,
            focus_ids=["08"],
            focus_region_xywh=[0.8, 0.8, 0.3, 0.3],
        )
    with pytest.raises(ValueError, match="normalized to 0-1"):
        SourceExtractionCommand(
            **base,
            visual_scope=VisualExtractionScope.SELECTED_DETAILS,
            focus_ids=["08"],
            focus_region_xywh=[float("nan"), 0.0, 0.5, 0.5],
        )
    with pytest.raises(ValueError, match="require selected_details"):
        SourceExtractionCommand(**base, focus_ids=["08"])
    with pytest.raises(ValueError, match="bounded identifiers"):
        SourceExtractionCommand(
            **base,
            visual_scope=VisualExtractionScope.SELECTED_DETAILS,
            focus_ids=["free form prompt"],
        )
    with pytest.raises(ValueError, match="require visual_structure"):
        SourceExtractionCommand(
            source_files=[r"C:\Source\report.pdf"],
            profile=SourceExtractionProfile.DOCUMENT_EVIDENCE,
            visual_scope=VisualExtractionScope.SELECTED_DETAILS,
            focus_ids=["08"],
            focus_region_xywh=[0.0, 0.0, 1.0, 1.0],
        )


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


def test_still_processing_failure_reports_slots_without_resubmit_signal(tmp_path: Path) -> None:
    class StillProcessingBackend(FakeQwenBackend):
        def extract(self, command, staged_sources, output_directory) -> SourceBackendResult:
            self.completed.set()
            error = QwenRequestStillProcessing("do not resubmit private source")
            error.latency_ms = 3_600_123
            error.timeout_phase = "read"
            error.provider_timeout = {
                "policy_name": "local_qwen_vision_v1",
                "connect_seconds": 30,
                "read_seconds": 5_400,
                "write_seconds": 3_600,
                "pool_seconds": 30,
            }
            raise error

    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "diagram.png"
    source.write_bytes(b"synthetic-image")
    backend = StillProcessingBackend()
    manager = SourceJobManager(
        backend=backend,
        jobs_root=tmp_path / "jobs",
        allowed_input_roots=[source_root],
    )

    submission = manager.submit(command(source, profile=SourceExtractionProfile.VISUAL_STRUCTURE))
    assert backend.completed.wait(timeout=2)
    report = wait_for_terminal(manager, submission.job_id)

    assert report.state is SourceJobState.FAILED
    assert report.failure_kind == "QwenRequestStillProcessing"
    assert report.latency_ms == 3_600_123
    assert report.timeout_phase == "read"
    assert report.progress_source == "llama_slots"
    assert report.upstream_progress_confirmed is True
    assert report.usage_observed is False
    assert report.provider_timeout is not None
    assert report.provider_timeout.read_seconds == 5_400
    assert "private source" not in report.detail


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
