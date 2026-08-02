"""Tests for exact-hash registered tool execution on staged copies."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ask_ai_mcp.models import (
    CandidateDecision,
    DeepSeekModel,
    ExecutionContract,
    RuntimeKind,
    ToolCapability,
    VerifiedToolContainerReport,
    VerifiedToolExecutionCommand,
    VerifiedToolExecutionStatus,
    VerifiedToolRecord,
)
from ask_ai_mcp.promotion import VerifiedToolRegistry
from ask_ai_mcp.sandbox import BACKEND_NAME, DEFAULT_RUNNER_IMAGE
from ask_ai_mcp.verified_execution import VerifiedToolExecutionError, VerifiedToolRunner


def make_registry(tmp_path: Path, *, dual: bool = True, runnable: bool = True):
    root = tmp_path / "registry"
    candidate_hash = "a" * 64
    target = root / "fixture_counter" / "0.1.0" / candidate_hash
    candidate_root = target / "candidate"
    candidate_root.mkdir(parents=True)
    source = (
        "def run(request, input_dir, output_dir):\n    return {'inputs': len(request['inputs'])}\n"
    )
    (candidate_root / "tool.py").write_bytes(source.encode("utf-8"))
    record = VerifiedToolRecord(
        name="fixture_counter",
        version="0.1.0",
        sha256=candidate_hash,
        source_job_id="52efb642-6d4a-42ea-9bbf-da5197360c77",
        spec_sha256="b" * 64,
        runner_image=DEFAULT_RUNNER_IMAGE,
        tests_run=2,
        file_sha256={
            "tool.py": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        },
        runtime=RuntimeKind.PYTHON,
        approved_at=datetime.now(UTC),
        approved_by="codex_desktop",
        approval_identities=(["codex_desktop", "claude_desktop"] if dual else ["codex_desktop"]),
        decision=CandidateDecision.APPROVED,
        allowed_capabilities=(
            [
                ToolCapability.READ_SYNTHETIC_INPUTS,
                ToolCapability.READ_COPIED_INPUTS,
                ToolCapability.WRITE_DEDICATED_OUTPUT,
            ]
            if runnable
            else [ToolCapability.READ_SYNTHETIC_INPUTS]
        ),
        build_model=DeepSeekModel.FLASH,
        entrypoint="tool.py" if runnable else None,
        execution_contract=ExecutionContract.JSON_FILES_V1 if runnable else None,
    )
    (target / "record.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return VerifiedToolRegistry(root), record


class FakeExecutor:
    def execute(self, **kwargs) -> VerifiedToolContainerReport:
        input_root = kwargs["input_root"]
        output_root = kwargs["output_root"]
        request = json.loads((input_root / "request.json").read_text(encoding="utf-8"))
        assert request["contract"] == "json_files_v1"
        (output_root / "_ask_ai_result.json").write_text(
            json.dumps({"count": len(request["inputs"])}), encoding="utf-8"
        )
        return VerifiedToolContainerReport(
            run_id=kwargs["run_id"],
            exit_code=0,
            backend=BACKEND_NAME,
            runner_image=DEFAULT_RUNNER_IMAGE,
        )


def command(record: VerifiedToolRecord, **updates) -> VerifiedToolExecutionCommand:
    values = {
        "name": record.name,
        "version": record.version,
        "candidate_sha256": record.sha256,
        "parameters_json": '{"mode":"count"}',
    }
    values.update(updates)
    return VerifiedToolExecutionCommand(**values)


def test_registered_tool_runs_only_on_staged_copy_and_records_hashes(tmp_path: Path) -> None:
    registry, record = make_registry(tmp_path)
    source_root = tmp_path / "allowed"
    source_root.mkdir()
    source = source_root / "business.xlsx"
    source.write_bytes(b"synthetic workbook bytes")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    runner = VerifiedToolRunner(
        registry=registry,
        runs_root=tmp_path / "runs",
        allowed_input_roots=[source_root],
        executor=FakeExecutor(),
    )

    report = runner.run(
        command(record, input_files=[str(source)]),
        client_name="codex_desktop",
    )

    assert report.status is VerifiedToolExecutionStatus.SUCCEEDED
    assert report.input_artifacts[0].staged_name == "input-0001.xlsx"
    assert report.input_artifacts[0].sha256 == original_hash
    assert source.read_bytes() == b"synthetic workbook bytes"
    assert {item.relative_path for item in report.output_artifacts} == {"_ask_ai_result.json"}
    assert Path(report.output_directory).is_relative_to((tmp_path / "runs").resolve())
    listed = runner.list_registered_tools().tools[0]
    assert listed.runnable is True
    assert listed.execution_count == 1


def test_input_outside_allow_list_is_rejected_before_executor(tmp_path: Path) -> None:
    registry, record = make_registry(tmp_path)
    source_root = tmp_path / "allowed"
    source_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("blocked", encoding="utf-8")
    runner = VerifiedToolRunner(
        registry=registry,
        runs_root=tmp_path / "runs",
        allowed_input_roots=[source_root],
        executor=FakeExecutor(),
    )

    with pytest.raises(VerifiedToolExecutionError, match="outside configured"):
        runner.run(
            command(record, input_files=[str(outside)]),
            client_name="codex_desktop",
        )


def test_dual_desktop_approval_is_required_for_output_tools(tmp_path: Path) -> None:
    registry, record = make_registry(tmp_path, dual=False)
    runner = VerifiedToolRunner(
        registry=registry,
        runs_root=tmp_path / "runs",
        allowed_input_roots=[],
        executor=FakeExecutor(),
    )

    with pytest.raises(VerifiedToolExecutionError, match="dual_desktop"):
        runner.run(command(record), client_name="codex_desktop")


def test_legacy_registration_is_listed_but_not_runnable(tmp_path: Path) -> None:
    registry, _ = make_registry(tmp_path, runnable=False)
    runner = VerifiedToolRunner(
        registry=registry,
        runs_root=tmp_path / "runs",
        allowed_input_roots=[],
        executor=FakeExecutor(),
    )

    summary = runner.list_registered_tools().tools[0]

    assert summary.runnable is False
    assert "execution_contract_missing" in summary.blocking_reasons
    assert "dedicated_output_not_approved" in summary.blocking_reasons
