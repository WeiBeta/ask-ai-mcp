"""Hash-pinned execution of approved tools against staged input copies."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from platformdirs import user_data_path

from ask_ai_mcp.models import (
    DeepSeekModel,
    ExecutionContract,
    RegisteredToolList,
    RegisteredToolSummary,
    RuntimeKind,
    ToolCapability,
    VerifiedInputArtifact,
    VerifiedOutputArtifact,
    VerifiedToolExecutionCommand,
    VerifiedToolExecutionReport,
    VerifiedToolExecutionStatus,
    VerifiedToolRecord,
)
from ask_ai_mcp.promotion import VerifiedToolRegistry
from ask_ai_mcp.sandbox import (
    DEFAULT_RUNNER_IMAGE,
    DockerVerifiedToolExecutor,
)

ALLOWED_INPUT_ROOTS_ENV = "ASK_AI_MCP_ALLOWED_INPUT_ROOTS"
MAX_INPUT_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_INPUT_BYTES = 500 * 1024 * 1024
MAX_OUTPUT_FILES = 100
MAX_TOTAL_OUTPUT_BYTES = 500 * 1024 * 1024
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


class VerifiedToolExecutionError(RuntimeError):
    """Raised when a registered tool cannot run inside the verified boundary."""


def default_runs_root() -> Path:
    return user_data_path("AskAIMCP", appauthor=False, ensure_exists=True) / "runs"


def load_allowed_input_roots() -> list[Path]:
    raw_value = os.environ.get(ALLOWED_INPUT_ROOTS_ENV, "")
    return [Path(value.strip()) for value in raw_value.split(";") if value.strip()]


class VerifiedToolRunner:
    def __init__(
        self,
        *,
        registry: VerifiedToolRegistry | None = None,
        runs_root: Path | None = None,
        allowed_input_roots: list[Path] | None = None,
        executor: DockerVerifiedToolExecutor | None = None,
    ) -> None:
        self.registry = registry or VerifiedToolRegistry()
        self.runs_root = (runs_root or default_runs_root()).resolve()
        self.runs_root.mkdir(parents=True, exist_ok=True)
        configured_roots = (
            allowed_input_roots if allowed_input_roots is not None else load_allowed_input_roots()
        )
        self.allowed_input_roots = [self._validate_allowed_root(root) for root in configured_roots]
        self.executor = executor or DockerVerifiedToolExecutor()

    def list_registered_tools(self) -> RegisteredToolList:
        counts = self._execution_counts()
        summaries = []
        for record in self.registry.list_records():
            reasons = self._blocking_reasons(record)
            summaries.append(
                RegisteredToolSummary(
                    record=record,
                    runnable=not reasons,
                    blocking_reasons=reasons,
                    execution_count=counts.get(self._identity(record), 0),
                )
            )
        return RegisteredToolList(tools=summaries)

    def run(
        self,
        command: VerifiedToolExecutionCommand,
        *,
        client_name: str,
    ) -> VerifiedToolExecutionReport:
        target, record = self.registry.load(
            name=command.name,
            version=command.version,
            candidate_sha256=command.candidate_sha256,
        )
        reasons = self._blocking_reasons(record)
        if reasons:
            raise VerifiedToolExecutionError(
                "registered tool is not runnable: " + ",".join(reasons)
            )
        if command.input_files:
            if ToolCapability.READ_COPIED_INPUTS not in record.allowed_capabilities:
                raise VerifiedToolExecutionError("tool is not approved to read copied inputs")
        elif ToolCapability.READ_SYNTHETIC_INPUTS not in record.allowed_capabilities:
            raise VerifiedToolExecutionError("tool is not approved for synthetic input")

        run_id = str(uuid4())
        run_root = self._within_runs_root(self.runs_root / run_id)
        input_root = run_root / "input"
        input_files_root = input_root / "files"
        output_root = run_root / "output"
        control_root = run_root / "control"
        input_files_root.mkdir(parents=True)
        output_root.mkdir()
        control_root.mkdir()
        started_at = datetime.now(UTC)

        input_artifacts = self._stage_inputs(command.input_files, input_files_root)
        request = {
            "contract": ExecutionContract.JSON_FILES_V1.value,
            "parameters": json.loads(command.parameters_json),
            "inputs": [artifact.model_dump(mode="json") for artifact in input_artifacts],
        }
        (input_root / "request.json").write_text(
            json.dumps(request, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

        container = self.executor.execute(
            run_id=run_id,
            candidate_root=target / "candidate",
            input_root=input_root,
            output_root=output_root,
            entrypoint=record.entrypoint or "",
        )
        output_artifacts = self._collect_outputs(output_root)
        succeeded = container.exit_code == 0 and not container.timed_out
        failure_reason = None if succeeded else "verified tool container execution failed"
        report = VerifiedToolExecutionReport(
            run_id=run_id,
            status=(
                VerifiedToolExecutionStatus.SUCCEEDED
                if succeeded
                else VerifiedToolExecutionStatus.FAILED
            ),
            tool_name=record.name,
            version=record.version,
            candidate_sha256=record.sha256,
            executed_by=client_name,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            backend=container.backend,
            runner_image=container.runner_image,
            exit_code=container.exit_code,
            timed_out=container.timed_out,
            output_truncated=container.output_truncated,
            input_artifacts=input_artifacts,
            output_artifacts=output_artifacts,
            output_directory=str(output_root),
            stdout=container.stdout,
            stderr=container.stderr,
            failure_reason=failure_reason,
        )
        self._write_report(control_root / "report.json", report)
        return report

    @staticmethod
    def _identity(record: VerifiedToolRecord) -> tuple[str, str, str]:
        return record.name, record.version, record.sha256

    @staticmethod
    def _blocking_reasons(record: VerifiedToolRecord) -> list[str]:
        reasons: list[str] = []
        if record.runtime is not RuntimeKind.PYTHON:
            reasons.append("runtime_not_supported")
        if record.runner_image != DEFAULT_RUNNER_IMAGE:
            reasons.append("runner_image_mismatch")
        if record.entrypoint is None or record.execution_contract is None:
            reasons.append("execution_contract_missing")
        elif record.execution_contract is not ExecutionContract.JSON_FILES_V1:
            reasons.append("execution_contract_not_supported")
        capabilities = set(record.allowed_capabilities)
        if ToolCapability.WRITE_DEDICATED_OUTPUT not in capabilities:
            reasons.append("dedicated_output_not_approved")
        if not capabilities.intersection(
            {ToolCapability.READ_SYNTHETIC_INPUTS, ToolCapability.READ_COPIED_INPUTS}
        ):
            reasons.append("input_read_not_approved")
        needs_dual = (
            record.build_model is DeepSeekModel.PRO
            or ToolCapability.WRITE_DEDICATED_OUTPUT in capabilities
        )
        if needs_dual and set(record.approval_identities) != {
            "claude_desktop",
            "codex_desktop",
        }:
            reasons.append("dual_desktop_approval_required")
        return reasons

    def _stage_inputs(
        self,
        source_paths: list[str],
        destination_root: Path,
    ) -> list[VerifiedInputArtifact]:
        artifacts: list[VerifiedInputArtifact] = []
        total_size = 0
        for index, raw_path in enumerate(source_paths, start=1):
            source = Path(raw_path)
            if not source.is_absolute():
                raise VerifiedToolExecutionError("input file paths must be absolute")
            resolved = source.resolve(strict=True)
            if not resolved.is_file() or self._is_link(source):
                raise VerifiedToolExecutionError("input path is not a plain file")
            if not any(resolved.is_relative_to(root) for root in self.allowed_input_roots):
                raise VerifiedToolExecutionError("input file is outside configured allowed roots")
            size = resolved.stat().st_size
            if size > MAX_INPUT_FILE_BYTES:
                raise VerifiedToolExecutionError("one input file exceeds the 100 MiB limit")
            total_size += size
            if total_size > MAX_TOTAL_INPUT_BYTES:
                raise VerifiedToolExecutionError("total input size exceeds the 500 MiB limit")
            suffix = resolved.suffix if _SAFE_SUFFIX.fullmatch(resolved.suffix) else ".bin"
            staged_name = f"input-{index:04d}{suffix.casefold()}"
            destination = destination_root / staged_name
            before_hash = self._hash_file(resolved)
            shutil.copyfile(resolved, destination)
            staged_hash = self._hash_file(destination)
            after_hash = self._hash_file(resolved)
            if before_hash != staged_hash or before_hash != after_hash:
                raise VerifiedToolExecutionError("input changed while its copy was staged")
            artifacts.append(
                VerifiedInputArtifact(
                    staged_name=staged_name,
                    sha256=staged_hash,
                    size_bytes=size,
                )
            )
        return artifacts

    def _collect_outputs(self, root: Path) -> list[VerifiedOutputArtifact]:
        artifacts: list[VerifiedOutputArtifact] = []
        total_size = 0
        for path in sorted(root.rglob("*")):
            if self._is_link(path):
                raise VerifiedToolExecutionError("output tree contains a link")
            if not path.is_file():
                continue
            if len(artifacts) >= MAX_OUTPUT_FILES:
                raise VerifiedToolExecutionError("output file count exceeds 100")
            size = path.stat().st_size
            total_size += size
            if total_size > MAX_TOTAL_OUTPUT_BYTES:
                raise VerifiedToolExecutionError("total output size exceeds 500 MiB")
            artifacts.append(
                VerifiedOutputArtifact(
                    relative_path=path.relative_to(root).as_posix(),
                    sha256=self._hash_file(path),
                    size_bytes=size,
                )
            )
        return artifacts

    def _execution_counts(self) -> dict[tuple[str, str, str], int]:
        counts: dict[tuple[str, str, str], int] = {}
        for path in self.runs_root.glob("*/control/report.json"):
            try:
                report = VerifiedToolExecutionReport.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            identity = (report.tool_name, report.version, report.candidate_sha256)
            counts[identity] = counts.get(identity, 0) + 1
        return counts

    @staticmethod
    def _validate_allowed_root(root: Path) -> Path:
        if not root.is_absolute():
            raise VerifiedToolExecutionError("allowed input roots must be absolute")
        resolved = root.resolve(strict=True)
        if not resolved.is_dir() or VerifiedToolRunner._is_link(root):
            raise VerifiedToolExecutionError("allowed input root is not a plain directory")
        if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
            raise VerifiedToolExecutionError("drive and user-profile roots are too broad")
        return resolved

    def _within_runs_root(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.runs_root):
            raise VerifiedToolExecutionError("run path escapes the configured runs root")
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

    @staticmethod
    def _write_report(path: Path, report: VerifiedToolExecutionReport) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
