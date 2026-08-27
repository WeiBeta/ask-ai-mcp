"""Offline tests for the bounded coding-candidate MCP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from ask_ai_mcp import server
from ask_ai_mcp.code_review_workspace import CodeReviewRepositoryCatalog
from ask_ai_mcp.coding import OPENCODE_GO_CHAT_URL, CodingManager
from ask_ai_mcp.coding_models import (
    ADVANCED_CODING_MODELS,
    DEFAULT_CODING_MODELS,
    CodingCandidatePayload,
    CodingJobState,
    CodingModel,
    CodingStatusCommand,
    CodingSubmitCommand,
    CodingTaskKind,
)
from ask_ai_mcp.coding_workspace import CodingSnapshotter
from ask_ai_mcp.opencode_account import OpenCodeAccount
from ask_ai_mcp.usage import UsageStore


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Ask AI Tests")
    (root / "Counter.cs").write_text(
        "public static class Counter { public static int Add(int x) => x + 1; }\n",
        encoding="utf-8",
    )
    (root / "CounterTests.cs").write_text("// tests\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _command(commit: str, model: CodingModel = CodingModel.GLM_5_3_FLASH):
    return CodingSubmitCommand(
        repository_id="unity",
        base_ref=commit,
        task_kind=CodingTaskKind.BUG_FIX,
        model=model,
        target_files=["Counter.cs"],
        context_files=["CounterTests.cs"],
        requirements=["Increment by two without changing the public method signature."],
        acceptance_tests=["The existing deterministic counter test must pass."],
    )


def test_coding_mcp_is_three_tools_and_not_part_of_full() -> None:
    coding = {tool.name for tool in asyncio.run(server.coding_mcp.list_tools())}
    full = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert coding == {"coding_backend_status", "coding_submit", "coding_status"}
    assert coding.isdisjoint(full)


def test_coding_schema_has_two_default_and_three_advanced_fixed_models() -> None:
    assert DEFAULT_CODING_MODELS == (
        CodingModel.DEEPSEEK_V4_FLASH,
        CodingModel.GLM_5_3_FLASH,
    )
    assert ADVANCED_CODING_MODELS == (
        CodingModel.DEEPSEEK_V4_PRO,
        CodingModel.GLM_5_3,
        CodingModel.KIMI_K3,
    )
    schema = CodingSubmitCommand.model_json_schema()
    assert schema["$defs"]["CodingModel"]["enum"] == [model.value for model in CodingModel]
    tools = asyncio.run(server.coding_mcp.list_tools())
    submit = next(tool for tool in tools if tool.name == "coding_submit")
    exposed = submit.parameters["properties"]["command"]["properties"]["model"]["enum"]
    assert exposed == [model.value for model in CodingModel]


def test_snapshot_is_frozen_and_excludes_uncommitted_content(tmp_path: Path) -> None:
    root, commit = _repository(tmp_path)
    (root / "Counter.cs").write_text("UNCOMMITTED\n", encoding="utf-8")
    snapshot = CodingSnapshotter(CodeReviewRepositoryCatalog({"unity": root})).capture(
        _command(commit)
    )
    assert snapshot.base_commit == commit
    assert "UNCOMMITTED" not in json.dumps([item.__dict__ for item in snapshot.files])
    assert snapshot.target_files["Counter.cs"].exists is True


def test_coding_catalog_reads_only_its_own_repository_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _commit = _repository(tmp_path)
    monkeypatch.setenv("ASK_AI_MCP_CODING_REPOSITORIES", json.dumps({"coding-repo": str(root)}))
    monkeypatch.setenv("ASK_AI_MCP_REVIEW_REPOSITORIES", "{}")

    snapshotter = CodingSnapshotter()

    assert snapshotter.catalog.repositories == {"coding-repo": root.resolve()}


@pytest.mark.parametrize("model", list(CodingModel))
def test_candidate_uses_fixed_model_max_reasoning_and_returns_external_diff(
    tmp_path: Path,
    model: CodingModel,
) -> None:
    root, commit = _repository(tmp_path)
    original = (root / "Counter.cs").read_text(encoding="utf-8")
    original_hash = hashlib.sha256(original.encode()).hexdigest()
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENCODE_GO_CHAT_URL
        body = json.loads(request.content)
        seen.append(body)
        assert body["model"] == model.value
        assert body["reasoning_effort"] == "max"
        assert body["max_tokens"] == 131_072
        assert str(root) not in body["messages"][1]["content"]
        payload = {
            "summary": "Increment by two.",
            "changes": [
                {
                    "file": "Counter.cs",
                    "original_sha256": original_hash,
                    "content": original.replace("x + 1", "x + 2"),
                }
            ],
            "suggested_tests": ["Run the deterministic counter test."],
            "risks": ["No overflow behavior change."],
            "truncated": False,
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {
                    "prompt_tokens": 1_000,
                    "prompt_tokens_details": {"cached_tokens": 500},
                    "completion_tokens": 200,
                    "completion_tokens_details": {"reasoning_tokens": 80},
                },
            },
        )

    usage = UsageStore(tmp_path / "usage.db")
    manager = CodingManager(
        usage_store=usage,
        snapshotter=CodingSnapshotter(CodeReviewRepositoryCatalog({"unity": root})),
        account=OpenCodeAccount(uid="go-user-01", alias="Go User"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
        state_root=tmp_path / "coding-state",
    )
    submission = manager.submit(_command(commit, model))
    assert submission.reasoning_effort == "max"
    assert submission.max_output_tokens == 131_072
    deadline = time.monotonic() + 5
    status = manager.status(CodingStatusCommand(job_id=submission.job_id, limit=1_000))
    while status.state in {CodingJobState.QUEUED, CodingJobState.RUNNING}:
        if time.monotonic() >= deadline:
            raise AssertionError("coding job did not complete")
        time.sleep(0.02)
        status = manager.status(CodingStatusCommand(job_id=submission.job_id, limit=1_000))

    assert status.state is CodingJobState.SUCCEEDED
    assert submission.model is model
    assert status.model is model
    assert status.reasoning_effort == "max"
    assert status.max_output_tokens == 131_072
    assert "x + 2" in status.patch_chunk
    assert status.changed_files == ["Counter.cs"]
    assert (root / "Counter.cs").read_text(encoding="utf-8") == original
    assert len(seen) == 1
    job = json.loads(
        (tmp_path / "coding-state" / "jobs" / submission.job_id / "job.json").read_text(
            encoding="utf-8"
        )
    )
    assert job["command"]["model"] == model.value
    assert job["reasoning_effort"] == "max"
    assert job["max_output_tokens"] == 131_072
    summary = usage.summarize(days=30)
    assert summary.opencode_go_accounts[0].account == "go-user-01"
    assert summary.by_model[model.value] == 1
    allowances = {item.model_id: item for item in summary.opencode_go_accounts[0].model_allowances}
    assert model.value in allowances
    assert summary.reasoning_tokens == 80


def test_coding_backend_reports_live_availability_for_all_five_models(tmp_path: Path) -> None:
    root, _commit = _repository(tmp_path)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "GET"
        return httpx.Response(200, json={"data": [{"id": model.value} for model in CodingModel]})

    manager = CodingManager(
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=CodingSnapshotter(CodeReviewRepositoryCatalog({"unity": root})),
        account=OpenCodeAccount(uid="go-user-01", alias="Go User"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
        state_root=tmp_path / "coding-state",
    )

    status = manager.backend_status(check_remote=True)

    assert calls == 1
    assert status.configured is True
    assert status.remote_models_checked is True
    assert [item.model_id for item in status.models] == list(CodingModel)
    assert all(item.available for item in status.models)
    assert all(item.requested_reasoning_effort == "max" for item in status.models)
    assert all(item.max_output_tokens == 131_072 for item in status.models)


@pytest.mark.parametrize("model", ADVANCED_CODING_MODELS)
def test_advanced_coding_failure_never_retries_or_falls_back(
    tmp_path: Path,
    model: CodingModel,
) -> None:
    root, commit = _repository(tmp_path)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(str(body["model"]))
        return httpx.Response(503, json={"error": {"message": "synthetic upstream failure"}})

    manager = CodingManager(
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=CodingSnapshotter(CodeReviewRepositoryCatalog({"unity": root})),
        account=OpenCodeAccount(uid="go-user-01", alias="Go User"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
        state_root=tmp_path / "coding-state",
    )
    submission = manager.submit(_command(commit, model))
    deadline = time.monotonic() + 5
    status = manager.status(CodingStatusCommand(job_id=submission.job_id))
    while status.state in {CodingJobState.QUEUED, CodingJobState.RUNNING}:
        if time.monotonic() >= deadline:
            raise AssertionError("coding job did not complete")
        time.sleep(0.02)
        status = manager.status(CodingStatusCommand(job_id=submission.job_id))

    assert status.state is CodingJobState.FAILED
    assert calls == [model.value]


def test_candidate_cannot_change_unlisted_file(tmp_path: Path) -> None:
    root, commit = _repository(tmp_path)
    snapshot = CodingSnapshotter(CodeReviewRepositoryCatalog({"unity": root})).capture(
        _command(commit)
    )
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "Bad candidate.",
                            "changes": [
                                {
                                    "file": "CounterTests.cs",
                                    "original_sha256": "0" * 64,
                                    "content": "changed\n",
                                }
                            ],
                            "suggested_tests": [],
                            "risks": [],
                            "truncated": False,
                        }
                    )
                }
            }
        ]
    }
    try:
        CodingManager._validated_payload(response, snapshot)
    except ValueError as error:
        assert "outside the target set" in str(error)
    else:
        raise AssertionError("unlisted file change was accepted")


def test_candidate_rejects_secret_metadata_and_separates_multiple_diffs(tmp_path: Path) -> None:
    root, _commit = _repository(tmp_path)
    (root / "Other.cs").write_text("OLD = 1\n", encoding="utf-8")
    _git(root, "add", "Other.cs")
    _git(root, "commit", "-m", "add other")
    commit = _git(root, "rev-parse", "HEAD")
    snapshotter = CodingSnapshotter(CodeReviewRepositoryCatalog({"unity": root}))
    command = _command(commit).model_copy(update={"target_files": ["Counter.cs", "Other.cs"]})
    snapshot = snapshotter.capture(command)
    payload = CodingCandidatePayload.model_validate(
        {
            "summary": "bounded change",
            "changes": [
                {
                    "file": item.path,
                    "original_sha256": item.sha256,
                    "content": "VALUE = 2",
                }
                for item in snapshot.files
                if item.target
            ],
            "suggested_tests": ["run unit tests"],
            "risks": [],
            "truncated": False,
        }
    )
    patch = CodingManager._diff(payload, snapshot)
    assert "VALUE = 2\ndiff --git a/Other.cs" in patch

    unsafe = payload.model_copy(update={"summary": 'api_key = "12345678901234567890"'})
    response = {"choices": [{"message": {"content": unsafe.model_dump_json()}}]}
    with pytest.raises(ValueError, match="metadata"):
        CodingManager._validated_payload(response, snapshot)


def test_validation_failure_preserves_response_audit_and_usage(tmp_path: Path) -> None:
    root, commit = _repository(tmp_path)
    usage = UsageStore(tmp_path / "usage.db")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"not_a_candidate":true}'}}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 4},
            },
        )

    manager = CodingManager(
        usage_store=usage,
        snapshotter=CodingSnapshotter(CodeReviewRepositoryCatalog({"unity": root})),
        account=OpenCodeAccount(uid="go-user@example.com", alias="go-user@example.com"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
        state_root=tmp_path / "coding-state",
    )
    submission = manager.submit(_command(commit))
    deadline = time.monotonic() + 5
    status = manager.status(CodingStatusCommand(job_id=submission.job_id))
    while status.state in {CodingJobState.QUEUED, CodingJobState.RUNNING}:
        if time.monotonic() >= deadline:
            raise AssertionError("coding job did not complete")
        time.sleep(0.02)
        status = manager.status(CodingStatusCommand(job_id=submission.job_id))

    assert status.state is CodingJobState.FAILED
    audit_path = (
        tmp_path / "coding-state" / "jobs" / submission.job_id / "audit" / "provider-response.json"
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["response_bytes"] > 0
    assert len(audit["response_sha256"]) == 64
    assert "choices" not in audit
    summary = usage.summarize(days=30)
    assert summary.total_calls == 1
    assert summary.failed_calls == 1
    assert summary.prompt_cache_miss_tokens == 25
