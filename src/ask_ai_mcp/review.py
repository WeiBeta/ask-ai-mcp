"""Persistence and revalidation for candidate review bundles."""

from __future__ import annotations

import difflib
import hashlib
import os
import stat
from pathlib import Path
from uuid import UUID

from ask_ai_mcp.hashing import candidate_payload_sha256, tool_spec_sha256
from ask_ai_mcp.models import (
    CandidateExecutionReport,
    CandidateFile,
    CandidateJobManifest,
    CandidateJobState,
    CandidateReviewBundle,
    StaticAnalysisReport,
    ToolBuildSpec,
    ToolCandidatePayload,
)
from ask_ai_mcp.workspace import default_jobs_root


class CandidateReviewError(RuntimeError):
    """Raised when persisted review evidence is missing or inconsistent."""


def candidate_patch(payload: ToolCandidatePayload) -> str:
    parts: list[str] = []
    for file in payload.files:
        diff = difflib.unified_diff(
            [],
            file.content.splitlines(),
            fromfile="/dev/null",
            tofile=f"b/{file.path}",
            lineterm="",
        )
        parts.append("\n".join(diff))
    return "\n\n".join(parts)


class CandidateReviewRepository:
    def __init__(self, jobs_root: Path | None = None) -> None:
        self.jobs_root = (jobs_root or default_jobs_root()).resolve()
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def save(self, review: CandidateReviewBundle) -> Path:
        job_root = self._job_root(review.job_id)
        path = job_root / "control" / "review.json"
        if path.exists():
            if self.load(review.job_id) != review:
                raise CandidateReviewError("a different review bundle already exists")
            return path
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(review.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(path)
            self.load(review.job_id)
        except Exception:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
        return path

    def load(self, job_id: str) -> CandidateReviewBundle:
        job_root = self._job_root(job_id)
        control_root = job_root / "control"
        manifest = CandidateJobManifest.model_validate_json(
            (control_root / "manifest.json").read_text(encoding="utf-8")
        )
        execution = CandidateExecutionReport.model_validate_json(
            (control_root / "execution.json").read_text(encoding="utf-8")
        )
        static_report = StaticAnalysisReport.model_validate_json(
            (control_root / "static-analysis.json").read_text(encoding="utf-8")
        )
        review = CandidateReviewBundle.model_validate_json(
            (control_root / "review.json").read_text(encoding="utf-8")
        )
        spec = ToolBuildSpec.model_validate_json(
            (control_root / "spec.json").read_text(encoding="utf-8")
        )

        if manifest.state not in {CandidateJobState.EXECUTED, CandidateJobState.APPROVED}:
            raise CandidateReviewError("candidate is not awaiting review")
        if (
            review.job_id != job_id
            or manifest.job_id != job_id
            or execution.job_id != job_id
            or review.candidate_sha256 != manifest.candidate_sha256
            or execution.candidate_sha256 != manifest.candidate_sha256
            or review.spec_sha256 != manifest.spec_sha256
            or review.execution != execution
            or review.static_analysis != static_report
            or tool_spec_sha256(spec) != manifest.spec_sha256
            or manifest.entrypoint != spec.entrypoint
            or manifest.execution_contract != spec.execution_contract
            or manifest.build_model != review.attempts[-1].model
        ):
            raise CandidateReviewError("review identity or evidence does not match the job")

        payload = ToolCandidatePayload(
            summary=review.candidate_summary,
            files=self._candidate_files(job_root / "candidate", manifest),
            risks=review.declared_risks,
        )
        if candidate_payload_sha256(payload) != manifest.candidate_sha256:
            raise CandidateReviewError("review metadata or candidate content changed")
        if review.candidate_files != [file.path for file in payload.files]:
            raise CandidateReviewError("review candidate file list changed")
        if review.candidate_patch != candidate_patch(payload):
            raise CandidateReviewError("review patch no longer matches candidate content")
        return review

    def _job_root(self, job_id: str) -> Path:
        try:
            identifier = UUID(job_id)
        except ValueError:
            raise CandidateReviewError("invalid candidate job ID") from None
        if identifier.version != 4 or str(identifier) != job_id:
            raise CandidateReviewError("invalid candidate job ID")
        root = (self.jobs_root / job_id).resolve(strict=True)
        if not root.is_relative_to(self.jobs_root) or root.name != job_id:
            raise CandidateReviewError("candidate job path escapes the jobs root")
        return root

    @classmethod
    def _candidate_files(
        cls,
        candidate_root: Path,
        manifest: CandidateJobManifest,
    ) -> list[CandidateFile]:
        if not candidate_root.is_dir() or cls._is_link(candidate_root):
            raise CandidateReviewError("candidate directory is missing or unsafe")
        for current, directories, files in os.walk(candidate_root, followlinks=False):
            for child_name in [*directories, *files]:
                if cls._is_link(Path(current, child_name)):
                    raise CandidateReviewError("candidate tree contains a link")

        actual_paths = sorted(
            path.relative_to(candidate_root).as_posix()
            for path in candidate_root.rglob("*")
            if path.is_file()
        )
        expected_paths = sorted(manifest.candidate_files)
        if (
            actual_paths != expected_paths
            or sorted(manifest.candidate_file_sha256) != expected_paths
        ):
            raise CandidateReviewError("candidate file set changed")

        candidate_files: list[CandidateFile] = []
        for relative_path in manifest.candidate_files:
            path = candidate_root / Path(*relative_path.split("/"))
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != manifest.candidate_file_sha256[relative_path]:
                raise CandidateReviewError("candidate file content changed")
            candidate_files.append(
                CandidateFile(path=relative_path, content=content.decode("utf-8"))
            )
        return candidate_files

    @staticmethod
    def _is_link(path: Path) -> bool:
        metadata = path.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        return path.is_symlink() or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
