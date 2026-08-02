"""Explicit, hash-pinned promotion of successfully tested candidates."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from platformdirs import user_data_path

from ask_ai_mcp.models import (
    CandidateApprovalRequest,
    CandidateDecision,
    CandidateExecutionReport,
    CandidateJobManifest,
    CandidateJobState,
    RuntimeKind,
    ToolBuildSpec,
    VerifiedToolRecord,
)
from ask_ai_mcp.sandbox import BACKEND_NAME, DEFAULT_RUNNER_IMAGE
from ask_ai_mcp.workspace import default_jobs_root


class CandidatePromotionError(RuntimeError):
    """Raised when a candidate cannot cross the explicit approval gate."""


def default_verified_tools_root() -> Path:
    return user_data_path("AskAIMCP", appauthor=False, ensure_exists=True) / "registry"


class VerifiedToolRegistry:
    """Copy exact approved bytes into an immutable-by-hash local registry."""

    def __init__(self, root: Path | None = None, *, jobs_root: Path | None = None) -> None:
        self.root = (root or default_verified_tools_root()).resolve()
        self.jobs_root = (jobs_root or default_jobs_root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def approve(
        self,
        *,
        job_root: Path,
        request: CandidateApprovalRequest,
    ) -> tuple[Path, VerifiedToolRecord]:
        if request.decision is not CandidateDecision.APPROVED:
            raise CandidatePromotionError(
                "approval with changes requires a new candidate hash and a fresh test run"
            )

        source_root = job_root.resolve(strict=True)
        if not source_root.is_relative_to(self.jobs_root):
            raise CandidatePromotionError("candidate job is outside the configured jobs root")
        manifest_path = source_root / "control" / "manifest.json"
        execution_path = source_root / "control" / "execution.json"
        manifest = CandidateJobManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        execution = CandidateExecutionReport.model_validate_json(
            execution_path.read_text(encoding="utf-8")
        )
        spec = ToolBuildSpec.model_validate_json(
            (source_root / "control" / "spec.json").read_text(encoding="utf-8")
        )

        candidate_root = source_root / "candidate"
        files = self._read_exact_files(candidate_root, manifest)
        target = self._target(manifest.tool_name, request.version, manifest.candidate_sha256)
        self._validate_gate(
            source_root,
            manifest,
            execution,
            request,
            spec,
            already_registered=target.exists(),
        )
        if target.exists():
            _, existing = self.load(
                name=manifest.tool_name,
                version=request.version,
                candidate_sha256=manifest.candidate_sha256,
            )
            if (
                existing.source_job_id != manifest.job_id
                or existing.spec_sha256 != manifest.spec_sha256
                or set(existing.allowed_capabilities) != set(request.allowed_capabilities)
            ):
                raise CandidatePromotionError("existing registration metadata does not match")
            identities = list(dict.fromkeys([*existing.approval_identities, request.approved_by]))
            if len(identities) > 2:
                raise CandidatePromotionError("only the two desktop controllers may approve")
            updated = existing.model_copy(update={"approval_identities": identities})
            self._write_json_atomic(target / "record.json", updated.model_dump_json(indent=2))
            return target, updated

        record = VerifiedToolRecord(
            name=manifest.tool_name,
            version=request.version,
            sha256=manifest.candidate_sha256,
            source_job_id=manifest.job_id,
            spec_sha256=manifest.spec_sha256,
            runner_image=execution.runner_image,
            tests_run=execution.tests_run,
            file_sha256=dict(manifest.candidate_file_sha256),
            runtime=RuntimeKind.PYTHON,
            approved_at=datetime.now(UTC),
            approved_by=request.approved_by,
            approval_identities=[request.approved_by],
            decision=request.decision,
            allowed_capabilities=request.allowed_capabilities,
            build_model=manifest.build_model,
            entrypoint=manifest.entrypoint,
            execution_contract=manifest.execution_contract,
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{manifest.candidate_sha256}.{uuid4().hex}.tmp"
        target_created = False
        try:
            (temporary / "candidate").mkdir(parents=True)
            for relative_path, content in files.items():
                destination = temporary / "candidate" / Path(*relative_path.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            (temporary / "record.json").write_text(
                record.model_dump_json(indent=2), encoding="utf-8"
            )
            temporary.rename(target)
            target_created = True
            updated_manifest = manifest.model_copy(update={"state": CandidateJobState.APPROVED})
            self._write_json_atomic(manifest_path, updated_manifest.model_dump_json(indent=2))
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            if target_created and target.exists():
                shutil.rmtree(target)
            raise
        return target, record

    def load(
        self,
        *,
        name: str,
        version: str,
        candidate_sha256: str,
    ) -> tuple[Path, VerifiedToolRecord]:
        target = self._target(name, version, candidate_sha256)
        record = VerifiedToolRecord.model_validate_json(
            (target / "record.json").read_text(encoding="utf-8")
        )
        if record.name != name or record.version != version or record.sha256 != candidate_sha256:
            raise CandidatePromotionError("registered record identity does not match its path")
        candidate_root = target / "candidate"
        actual = self._hash_tree(candidate_root)
        if actual != record.file_sha256:
            raise CandidatePromotionError("registered candidate files no longer match approval")
        return target, record

    def _target(self, name: str, version: str, candidate_sha256: str) -> Path:
        target = (self.root / name / version / candidate_sha256).resolve()
        if not target.is_relative_to(self.root):
            raise CandidatePromotionError("verified tool path escapes the registry")
        return target

    @staticmethod
    def _validate_gate(
        source_root: Path,
        manifest: CandidateJobManifest,
        execution: CandidateExecutionReport,
        request: CandidateApprovalRequest,
        spec: ToolBuildSpec,
        *,
        already_registered: bool,
    ) -> None:
        if source_root.name != manifest.job_id or request.job_id != manifest.job_id:
            raise CandidatePromotionError("approval job identity mismatch")
        if request.candidate_sha256 != manifest.candidate_sha256:
            raise CandidatePromotionError("approval candidate hash mismatch")
        expected_state = (
            {CandidateJobState.EXECUTED, CandidateJobState.APPROVED}
            if already_registered
            else {CandidateJobState.EXECUTED}
        )
        if manifest.state not in expected_state:
            raise CandidatePromotionError("candidate job has not passed isolated execution")
        if (
            manifest.entrypoint is None
            or manifest.execution_contract is None
            or manifest.entrypoint != spec.entrypoint
            or manifest.execution_contract != spec.execution_contract
        ):
            raise CandidatePromotionError("candidate execution contract is missing or changed")
        if (
            execution.job_id != manifest.job_id
            or execution.candidate_sha256 != manifest.candidate_sha256
            or execution.state is not CandidateJobState.EXECUTED
            or execution.exit_code != 0
            or execution.timed_out
            or execution.tests_run < 1
            or execution.backend != BACKEND_NAME
            or execution.runner_image != DEFAULT_RUNNER_IMAGE
            or manifest.execution_backend != BACKEND_NAME
        ):
            raise CandidatePromotionError("execution report does not qualify for approval")

    def list_records(self) -> list[VerifiedToolRecord]:
        records: list[VerifiedToolRecord] = []
        for record_path in sorted(self.root.rglob("record.json")):
            relative = record_path.relative_to(self.root)
            if len(relative.parts) != 4:
                raise CandidatePromotionError("registered record path has an invalid shape")
            name, version, candidate_sha256, _ = relative.parts
            _, record = self.load(
                name=name,
                version=version,
                candidate_sha256=candidate_sha256,
            )
            records.append(record)
        return records

    @classmethod
    def _read_exact_files(
        cls,
        candidate_root: Path,
        manifest: CandidateJobManifest,
    ) -> dict[str, bytes]:
        if not candidate_root.is_dir() or cls._is_link(candidate_root):
            raise CandidatePromotionError("candidate directory is missing or unsafe")
        for current, directories, files in os.walk(candidate_root, followlinks=False):
            for child_name in [*directories, *files]:
                if cls._is_link(Path(current, child_name)):
                    raise CandidatePromotionError("candidate tree contains a link")

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
            raise CandidatePromotionError("candidate file set changed after execution")

        contents: dict[str, bytes] = {}
        for relative_path in expected_paths:
            content = (candidate_root / Path(*relative_path.split("/"))).read_bytes()
            if hashlib.sha256(content).hexdigest() != manifest.candidate_file_sha256[relative_path]:
                raise CandidatePromotionError("candidate file changed after execution")
            contents[relative_path] = content
        return contents

    @classmethod
    def _hash_tree(cls, candidate_root: Path) -> dict[str, str]:
        if not candidate_root.is_dir() or cls._is_link(candidate_root):
            raise CandidatePromotionError("registered candidate directory is unsafe")
        hashes: dict[str, str] = {}
        for path in candidate_root.rglob("*"):
            if cls._is_link(path):
                raise CandidatePromotionError("registered candidate tree contains a link")
            if path.is_file():
                hashes[path.relative_to(candidate_root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        return hashes

    @staticmethod
    def _is_link(path: Path) -> bool:
        metadata = path.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        return path.is_symlink() or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)

    @staticmethod
    def _write_json_atomic(path: Path, content: str) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
