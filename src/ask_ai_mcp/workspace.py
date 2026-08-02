"""Materialize statically approved candidates outside the repository."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from uuid import uuid4

from platformdirs import user_data_path

from ask_ai_mcp.hashing import candidate_payload_sha256, tool_spec_sha256
from ask_ai_mcp.models import (
    CandidateJobManifest,
    CandidateJobState,
    StaticAnalysisReport,
    ToolBuildSpec,
    ToolCandidateResult,
)
from ask_ai_mcp.static_policy import analyze_candidate


class CandidateWorkspaceError(RuntimeError):
    """Raised when an untrusted candidate cannot be safely staged."""


def default_jobs_root() -> Path:
    return user_data_path("AskAIMCP", appauthor=False, ensure_exists=True) / "jobs"


class CandidateWorkspaceManager:
    def __init__(self, jobs_root: Path | None = None) -> None:
        self.jobs_root = (jobs_root or default_jobs_root()).resolve()
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def stage(
        self,
        *,
        spec: ToolBuildSpec,
        result: ToolCandidateResult,
    ) -> tuple[Path, CandidateJobManifest, StaticAnalysisReport]:
        actual_hash = candidate_payload_sha256(result.payload)
        if result.candidate_sha256 != actual_hash:
            raise CandidateWorkspaceError("candidate hash does not match validated content")
        analysis = analyze_candidate(spec, result.payload)
        if not analysis.allowed:
            raise CandidateWorkspaceError("static policy rejected the candidate")

        job_id = str(uuid4())
        job_root = self._within_jobs_root(self.jobs_root / job_id)
        candidate_root = job_root / "candidate"
        (job_root / "control").mkdir(parents=True)
        (job_root / "input").mkdir()
        candidate_root.mkdir()
        (job_root / "output").mkdir()

        for candidate_file in result.payload.files:
            relative = Path(*PurePosixPath(candidate_file.path).parts)
            target = self._within(candidate_root, candidate_root / relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(candidate_file.content.encode("utf-8"))

        manifest = CandidateJobManifest(
            job_id=job_id,
            state=CandidateJobState.STATIC_APPROVED,
            tool_name=spec.name,
            spec_sha256=tool_spec_sha256(spec),
            candidate_sha256=actual_hash,
            candidate_files=[file.path for file in result.payload.files],
            candidate_file_sha256={
                file.path: hashlib.sha256(file.content.encode("utf-8")).hexdigest()
                for file in result.payload.files
            },
        )
        (job_root / "control" / "manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        (job_root / "control" / "static-analysis.json").write_text(
            analysis.model_dump_json(indent=2), encoding="utf-8"
        )
        return job_root, manifest, analysis

    def _within_jobs_root(self, path: Path) -> Path:
        return self._within(self.jobs_root, path)

    @staticmethod
    def _within(root: Path, path: Path) -> Path:
        resolved_root = root.resolve()
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(resolved_root):
            raise CandidateWorkspaceError("path escapes candidate workspace")
        return resolved_path
