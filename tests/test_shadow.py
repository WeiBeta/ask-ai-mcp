"""Tests for replay-backed, mechanically labelled Qwen shadow comparisons."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from ask_ai_mcp.hashing import candidate_payload_sha256
from ask_ai_mcp.models import (
    CandidateAttemptReport,
    CandidateExecutionReport,
    CandidateFile,
    CandidateJobState,
    DeepSeekModel,
    ModelProvider,
    ToolBuildSpec,
    ToolCandidatePayload,
    ToolCategory,
)
from ask_ai_mcp.replay import ReplayAttempt, ReplayCaptureError, ToolsmithReplayCapsule
from ask_ai_mcp.shadow import (
    ReferenceTestGrader,
    ShadowComparisonStore,
    ShadowLifecycleGrade,
    ToolsmithShadowComparison,
)
from ask_ai_mcp.static_policy import analyze_candidate


def spec() -> ToolBuildSpec:
    return ToolBuildSpec(
        name="shadow_fixture",
        category=ToolCategory.TEST_UTILITY,
        purpose="Return one deterministic value from a synthetic JSON request.",
        input_contract="Accept a JSON object with one optional string value.",
        output_contract="Return a JSON object containing the same string value.",
        acceptance_tests=["A stdlib unittest verifies deterministic value copying."],
    )


def payload(test_marker: str) -> ToolCandidatePayload:
    return ToolCandidatePayload(
        summary="Synthetic shadow candidate.",
        files=[
            CandidateFile(
                path="tool.py",
                content=(
                    "def run(request, input_dir, output_dir):\n"
                    "    return {'value': request.get('value')}\n"
                ),
            ),
            CandidateFile(
                path=f"test_{test_marker}.py",
                content=(
                    "import unittest\n"
                    "from tool import run\n\n"
                    "class ToolTests(unittest.TestCase):\n"
                    "    def test_value(self):\n"
                    f"        self.assertEqual(run({{'value': '{test_marker}'}}, None, None), "
                    f"{{'value': '{test_marker}'}})\n"
                ),
            ),
        ],
    )


def capsule(provider: ModelProvider, candidate: ToolCandidatePayload) -> ToolsmithReplayCapsule:
    build_spec = spec()
    digest = candidate_payload_sha256(candidate)
    static = analyze_candidate(build_spec, candidate)
    return ToolsmithReplayCapsule(
        lifecycle_id=str(uuid4()),
        budget_session_id=str(uuid4()),
        client_name="synthetic",
        prompt_template_version="synthetic-v1",
        prompt_template_sha256="a" * 64,
        spec=build_spec,
        attempts=[
            ReplayAttempt(
                report=CandidateAttemptReport(
                    attempt=1,
                    candidate_sha256=digest,
                    model=DeepSeekModel.FLASH,
                    provider=provider,
                    thinking_enabled=True,
                    state=CandidateJobState.STATIC_APPROVED,
                    static_analysis=static,
                ),
                candidate=candidate,
            )
        ],
    )


def test_cross_grade_replaces_challenger_self_tests_with_reference_tests(tmp_path: Path) -> None:
    reference = capsule(ModelProvider.DEEPSEEK, payload("reference"))
    challenger = capsule(ModelProvider.LOCAL_QWEN, payload("challenger"))

    class FakeWorkspace:
        def __init__(self) -> None:
            self.result = None

        def stage(self, *, spec, result):
            self.result = result
            return tmp_path, None, None

    class FakeExecutor:
        def __init__(self, workspace: FakeWorkspace) -> None:
            self.workspace = workspace

        def execute(self, _job_root):
            return CandidateExecutionReport(
                job_id=str(uuid4()),
                candidate_sha256=self.workspace.result.candidate_sha256,
                state=CandidateJobState.EXECUTED,
                backend="synthetic",
                runner_image="synthetic@sha256:" + "b" * 64,
                exit_code=0,
                tests_run=1,
            )

    workspace = FakeWorkspace()
    grade = ReferenceTestGrader(
        workspace=workspace,
        executor=FakeExecutor(workspace),
    ).grade(reference, challenger)

    paths = {file.path for file in workspace.result.payload.files}
    assert "test_reference.py" in paths
    assert "test_challenger.py" not in paths
    assert grade.passed_reference_tests is True


def test_shadow_comparison_store_is_hash_protected_and_immutable(tmp_path: Path) -> None:
    reference_id = str(uuid4())
    challenger_id = str(uuid4())
    comparison = ToolsmithShadowComparison(
        spec_sha256="c" * 64,
        reference_prompt_sha256="d" * 64,
        challenger_prompt_sha256="e" * 64,
        reference=ShadowLifecycleGrade(
            lifecycle_id=reference_id,
            provider=ModelProvider.DEEPSEEK,
            attempt_count=1,
        ),
        challenger=ShadowLifecycleGrade(
            lifecycle_id=challenger_id,
            provider=ModelProvider.LOCAL_QWEN,
            attempt_count=1,
        ),
    )
    store = ShadowComparisonStore(tmp_path)

    path = store.write(comparison)

    assert path.is_file()
    assert path.with_suffix(".sha256").is_file()
    changed = comparison.model_copy(update={"warnings": ["different"]})
    with pytest.raises(ReplayCaptureError, match="immutable"):
        store.write(changed)
