"""Tests for the isolated, fixed-model code-review MCP module."""

from __future__ import annotations

import asyncio
import hashlib
import json
import stat
import subprocess
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from ask_ai_mcp import server
from ask_ai_mcp.code_review import OPENCODE_GO_CHAT_URL, CodeReviewManager
from ask_ai_mcp.code_review_models import (
    CodeReviewAdjudicationCommand,
    CodeReviewAdjudicationDecision,
    CodeReviewExternalUsageCommand,
    CodeReviewFailureCode,
    CodeReviewJobState,
    CodeReviewModel,
    CodeReviewProfile,
    CodeReviewProviderFailureClass,
    CodeReviewStagePatchCommand,
    CodeReviewStatusCommand,
    CodeReviewSubmitCommand,
    CodeReviewValidationStage,
)
from ask_ai_mcp.code_review_store import CodeReviewStore
from ask_ai_mcp.code_review_workspace import (
    CodeReviewRepositoryCatalog,
    CodeReviewSnapshot,
    CodeReviewSnapshotter,
    CodeReviewWorkspaceError,
)
from ask_ai_mcp.opencode_account import OpenCodeAccount
from ask_ai_mcp.opencode_protocol import OPENCODE_GO_RESPONSES_URL, OpenCodeProviderResponseError
from ask_ai_mcp.usage import UsageStore
from ask_ai_mcp.wire_capture import decrypt_wire_file


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path, relative_file: str = "app.py") -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Ask AI Tests")
    source = root / relative_file
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(root, "add", relative_file)
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    source.write_text(
        "def add(a, b):\n    if a is None:\n        return 0\n    return a + b\n",
        encoding="utf-8",
    )
    _git(root, "add", relative_file)
    _git(root, "commit", "-m", "change")
    head = _git(root, "rev-parse", "HEAD")
    return root, base, head


def _valid_review_payload() -> dict[str, object]:
    return {
        "findings": [
            {
                "finding_id": "valid_finding",
                "category": "correctness",
                "severity": "medium",
                "confidence": 0.9,
                "file": "app.py",
                "line_start": 2,
                "line_end": 3,
                "evidence_summary": "Bounded evidence marker that must not enter audit metadata.",
                "evidence_sha256": "0" * 64,
                "rationale": "Bounded rationale marker that must not enter audit metadata.",
                "suggested_validation_test": "Run one bounded test.",
            }
        ],
        "omitted_context": [],
        "truncated": False,
    }


def _run_review_fixture(
    tmp_path: Path,
    *,
    content: object,
    usage: dict[str, object] | None = None,
    choices_override: object | None = None,
    repository_file: str = "app.py",
) -> tuple[object, dict[str, object], int, Path]:
    root, base, head = _repository(tmp_path, repository_file)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "choices": (
                    choices_override
                    if choices_override is not None
                    else [{"finish_reason": "stop", "message": {"content": content}}]
                ),
                "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    store = CodeReviewStore(tmp_path / "review-state")
    manager = CodeReviewManager(
        store=store,
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})),
        account=OpenCodeAccount(uid="go-user@example.com", alias="go-user@example.com"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
    )
    submission = manager.submit(
        CodeReviewSubmitCommand(
            repository_id="sample",
            base_ref=base,
            head_ref=head,
            review_profile=CodeReviewProfile.DATA_INTEGRITY,
            model=CodeReviewModel.GLM_5_3,
        )
    )
    deadline = time.monotonic() + 5
    status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))
    while status.state in {CodeReviewJobState.QUEUED, CodeReviewJobState.RUNNING}:
        if time.monotonic() >= deadline:
            raise AssertionError("review job did not complete")
        time.sleep(0.02)
        status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))
    audit = json.loads(
        (store.jobs_root / submission.job_id / "audit" / "provider-response.json").read_text(
            encoding="utf-8"
        )
    )
    return status, audit, calls, root


def test_review_mcp_is_a_four_tool_surface_excluded_from_full() -> None:
    review_tools = {tool.name for tool in asyncio.run(server.review_mcp.list_tools())}
    full_tools = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert review_tools == {
        "code_review_backend_status",
        "code_review_stage_patch",
        "code_review_submit",
        "code_review_status",
    }
    assert review_tools.isdisjoint(full_tools)


def test_snapshot_uses_commit_hashes_and_never_exposes_host_root(tmp_path: Path) -> None:
    root, base, head = _repository(tmp_path)
    (root / "app.py").write_text("UNCOMMITTED_WORKTREE_CONTENT\n", encoding="utf-8")
    snapshot = CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})).from_refs(
        "sample", base, head
    )

    assert snapshot.changed_files == ("app.py",)
    assert snapshot.source_identity == {"base_commit": base, "head_commit": head}
    assert str(root) not in snapshot.diff_text
    assert str(root) not in json.dumps(snapshot.context)
    assert "UNCOMMITTED_WORKTREE_CONTENT" not in json.dumps(snapshot.context)
    assert "if a is None" in json.dumps(snapshot.context)
    assert snapshot.changed_line_count == 2


def test_patch_traversal_and_secret_bearing_diffs_are_rejected(tmp_path: Path) -> None:
    root, _, _ = _repository(tmp_path)
    patch_root = tmp_path / "sample"
    patch_root.mkdir()
    snapshotter = CodeReviewSnapshotter(
        CodeReviewRepositoryCatalog({"sample": root}), patch_roots=(patch_root,)
    )
    traversal = b"diff --git a/../secret.py b/../secret.py\n+print('x')\n"
    with pytest.raises(CodeReviewWorkspaceError, match="relative path"):
        snapshotter.stage_patch("sample", traversal.decode())

    secret = (
        b"diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n+API_KEY='abcdefghijklmnopqrstuvwxyz'\n"
    )
    with pytest.raises(CodeReviewWorkspaceError, match="no reviewable"):
        snapshotter.stage_patch("sample", secret.decode())
    unsanitized = (
        "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
        "@@ -1 +1 @@\n-old\n+C:\\Users\\person\\private.txt\n"
    )
    with pytest.raises(CodeReviewWorkspaceError, match="fully sanitized review diff"):
        snapshotter.stage_patch("sample", unsanitized)


def test_patch_only_preflight_is_hash_pinned_and_root_bounded(tmp_path: Path) -> None:
    root, base, head = _repository(tmp_path)
    patch_root = tmp_path / "sample"
    patch_root.mkdir()
    patch_text = _git(root, "diff", "--no-renames", "--unified=3", base, head) + "\n"
    patch_bytes = patch_text.encode("utf-8")
    expected_sha256 = hashlib.sha256(patch_bytes).hexdigest()
    snapshotter = CodeReviewSnapshotter(
        CodeReviewRepositoryCatalog({"sample": root}), patch_roots=(patch_root,)
    )
    (root / "app.py").write_text("UNCOMMITTED_WORKTREE_CONTENT\n", encoding="utf-8")

    staged = snapshotter.stage_patch("sample", patch_text)
    patch_path = patch_root / f"{expected_sha256}.patch"
    receipt_path = patch_root / f"{expected_sha256}.receipt.json"
    snapshot = snapshotter.from_staged_patch("sample", staged.patch_sha256, staged.receipt_sha256)

    assert staged.byte_length == len(patch_bytes)
    assert patch_path.stat().st_size == len(patch_bytes)
    assert hashlib.sha256(patch_path.read_bytes()).hexdigest() == expected_sha256
    assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == staged.receipt_sha256
    assert patch_path.stat().st_mode & stat.S_IWRITE == 0
    assert receipt_path.stat().st_mode & stat.S_IWRITE == 0
    assert not list(patch_root.glob(".*.tmp"))
    assert snapshotter.stage_patch("sample", patch_text) == staged
    assert snapshot.source_identity == {"patch_sha256": expected_sha256}
    assert snapshot.changed_files == ("app.py",)
    assert snapshot.context == ()
    assert "UNCOMMITTED_WORKTREE_CONTENT" not in snapshot.diff_text
    with pytest.raises(CodeReviewWorkspaceError, match="receipt SHA-256 does not match"):
        snapshotter.from_staged_patch("sample", expected_sha256, "0" * 64)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["byte_length"] += 1
    tampered = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    receipt_path.chmod(stat.S_IWRITE | stat.S_IREAD)
    receipt_path.write_bytes(tampered)
    receipt_path.chmod(stat.S_IREAD)
    with pytest.raises(CodeReviewWorkspaceError, match="receipt does not match its patch"):
        snapshotter.from_staged_patch(
            "sample", expected_sha256, hashlib.sha256(tampered).hexdigest()
        )


def test_staged_patch_requires_matching_pair_and_project_root(tmp_path: Path) -> None:
    root, base, head = _repository(tmp_path)
    wrong_root = tmp_path / "other-project"
    wrong_root.mkdir()
    patch_text = _git(root, "diff", "--no-renames", "--unified=3", base, head) + "\n"
    snapshotter = CodeReviewSnapshotter(
        CodeReviewRepositoryCatalog({"sample": root}), patch_roots=(wrong_root,)
    )

    with pytest.raises(CodeReviewWorkspaceError, match="project-specific patch root"):
        snapshotter.stage_patch("sample", patch_text)

    patch_root = tmp_path / "sample"
    patch_root.mkdir()
    snapshotter = CodeReviewSnapshotter(
        CodeReviewRepositoryCatalog({"sample": root}), patch_roots=(patch_root,)
    )
    patch_sha256 = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
    (patch_root / f"{patch_sha256}.patch").write_text(patch_text, encoding="utf-8")

    with pytest.raises(CodeReviewWorkspaceError, match="must both exist"):
        snapshotter.from_staged_patch("sample", patch_sha256, "0" * 64)


def test_stage_patch_is_free_and_submit_requires_receipt_hash(tmp_path: Path) -> None:
    root, base, head = _repository(tmp_path)
    patch_root = tmp_path / "sample"
    patch_root.mkdir()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    manager = CodeReviewManager(
        store=CodeReviewStore(tmp_path / "review-state"),
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=CodeReviewSnapshotter(
            CodeReviewRepositoryCatalog({"sample": root}), patch_roots=(patch_root,)
        ),
        account=OpenCodeAccount(uid="go-test-uid", alias="go-test"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
    )
    patch_text = _git(root, "diff", "--no-renames", "--unified=3", base, head) + "\n"

    staged = manager.stage_patch(
        CodeReviewStagePatchCommand(repository_id="sample", patch=patch_text)
    )

    assert calls == 0
    assert staged.byte_length == len(patch_text.encode("utf-8"))
    with pytest.raises(ValueError, match="receipt_sha256"):
        CodeReviewSubmitCommand(
            repository_id="sample",
            patch_sha256=staged.patch_sha256,
            review_profile=CodeReviewProfile.GENERAL,
            model=CodeReviewModel.GLM_5_3,
        )
    receipt_path = patch_root / f"{staged.patch_sha256}.receipt.json"
    receipt_path.chmod(stat.S_IWRITE | stat.S_IREAD)
    receipt_path.write_bytes(b"{}\n")
    receipt_path.chmod(stat.S_IREAD)
    with pytest.raises(CodeReviewWorkspaceError, match="receipt SHA-256 does not match"):
        manager.submit(
            CodeReviewSubmitCommand(
                repository_id="sample",
                patch_sha256=staged.patch_sha256,
                receipt_sha256=staged.receipt_sha256,
                review_profile=CodeReviewProfile.GENERAL,
                model=CodeReviewModel.GLM_5_3,
            )
        )
    assert calls == 0


def test_submodule_pointer_changes_are_not_sent_for_review(tmp_path: Path) -> None:
    root, _, _ = _repository(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    _git(child, "init")
    _git(child, "config", "user.email", "tests@example.invalid")
    _git(child, "config", "user.name", "Ask AI Tests")
    (child / "value.txt").write_text("one\n", encoding="utf-8")
    _git(child, "add", "value.txt")
    _git(child, "commit", "-m", "one")
    first = _git(child, "rev-parse", "HEAD")
    (child / "value.txt").write_text("two\n", encoding="utf-8")
    _git(child, "commit", "-am", "two")
    second = _git(child, "rev-parse", "HEAD")

    _git(root, "update-index", "--add", "--cacheinfo", f"160000,{first},deps/child")
    _git(root, "commit", "-m", "add submodule pointer")
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "update-index", "--cacheinfo", f"160000,{second},deps/child")
    _git(root, "commit", "-m", "move submodule pointer")
    head = _git(root, "rev-parse", "HEAD")

    snapshotter = CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root}))
    with pytest.raises(CodeReviewWorkspaceError, match="no reviewable"):
        snapshotter.from_refs("sample", base, head)


def test_review_job_is_blind_paginated_and_records_adjudication(
    tmp_path: Path, monkeypatch
) -> None:
    root, base, head = _repository(tmp_path)
    seen_prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENCODE_GO_CHAT_URL
        assert request.headers["Authorization"] == "Bearer opaque-test-key-1234567890"
        body = json.loads(request.content)
        assert body["model"] == CodeReviewModel.GLM_5_3.value
        assert body["temperature"] == 0
        assert body["reasoning_effort"] == "max"
        assert body["max_tokens"] == 131_072
        assert body["response_format"] == {"type": "json_object"}
        prompt = body["messages"][1]["content"]
        seen_prompts.append(prompt)
        assert str(root) not in prompt
        assert "app.py" in prompt
        assert (
            "Every finding.category must be exactly one of: correctness, security, reliability, "
            "performance, maintainability, testing."
        ) in prompt
        assert (
            'Every finding.file must exactly copy one string from this JSON array: ["app.py"].'
        ) in prompt
        assert "do not add Git a/ or b/ prefixes" in prompt
        payload = {
            "findings": [
                {
                    "finding_id": "none_semantics",
                    "category": "regression",
                    "severity": "medium",
                    "confidence": 0.9,
                    "file": "app.py",
                    "line_start": 2,
                    "line_end": 3,
                    "evidence_summary": "Returning zero changes None handling semantics.",
                    "evidence_sha256": "0" * 64,
                    "rationale": "Callers may expect a TypeError rather than silent coercion.",
                    "suggested_validation_test": "Add a test for add(None, 1).",
                }
            ],
            "omitted_context": [],
            "truncated": False,
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}],
                "usage": {
                    "prompt_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 20},
                    "completion_tokens": 50,
                },
            },
        )

    manager = CodeReviewManager(
        store=CodeReviewStore(tmp_path / "review-state"),
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})),
        account=OpenCodeAccount(uid="go-test-uid", alias="go-test"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
    )
    submission = manager.submit(
        CodeReviewSubmitCommand(
            repository_id="sample",
            base_ref=base,
            head_ref=head,
            review_profile=CodeReviewProfile.GENERAL,
            model=CodeReviewModel.GLM_5_3,
        )
    )
    deadline = time.monotonic() + 5
    status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id, limit=1))
    while status.state in {CodeReviewJobState.QUEUED, CodeReviewJobState.RUNNING}:
        if time.monotonic() >= deadline:
            raise AssertionError("review job did not complete")
        time.sleep(0.02)
        status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id, limit=1))

    assert status.state is CodeReviewJobState.SUCCEEDED
    manifest = json.loads(
        (manager.store.jobs_root / submission.job_id / "input" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["prompt_version"] == "code-review-prompt-v3"
    assert manifest["reasoning_effort"] == "max"
    assert manifest["max_output_tokens"] == 131_072
    run = manager.store.get_run(submission.job_id)
    assert run["reasoning_effort"] == "max"
    assert run["max_output_tokens"] == 131_072
    import sqlite3

    with sqlite3.connect(tmp_path / "usage.db") as connection:
        usage_policy = connection.execute(
            "SELECT reasoning_effort, max_output_tokens FROM api_usage"
        ).fetchone()
    assert usage_policy == ("max", 131_072)
    assert status.model_identity_hidden is True
    assert status.total_findings == 1
    assert status.findings[0].category.value == "correctness"
    assert (
        status.findings[0].evidence_sha256
        == hashlib.sha256(status.findings[0].evidence_summary.encode()).hexdigest()
    )
    assert all(
        artifact.relative_path.startswith(("input/", "output/", "audit/"))
        for artifact in status.artifacts
    )
    assert seen_prompts

    adjudicated = manager.status(
        CodeReviewStatusCommand(
            job_id=submission.job_id,
            adjudication=CodeReviewAdjudicationCommand(
                finding_id="none_semantics",
                decision=CodeReviewAdjudicationDecision.TRUE_POSITIVE,
                severity_agreement=True,
                accepted=True,
                fixed=False,
                test_confirmed=True,
                adjudication_ms=250,
            ),
        )
    )
    assert adjudicated.model_identity_hidden is True
    report = manager.store.monthly_report()
    language = next(item for item in report.slices if item.dimension == "language")
    assert language.precision == 1.0
    assert language.unique_true_findings == 1
    assert language.test_confirmed_rate == 1.0
    assert language.escaped_defects == 0
    assert language.latency_per_accepted_finding_ms is not None


def test_group_binding_rejects_a_changed_diff_contract(tmp_path: Path) -> None:
    store = CodeReviewStore(tmp_path / "state")
    base = {
        "run_id": "1",
        "review_group_id": "group",
        "blind_label": "review-11111111",
        "repository_id": "sample",
        "repo_snapshot_hash": "a" * 64,
        "diff_hash": "b" * 64,
        "model": "glm-5.3",
        "provider": "opencode",
        "protocol": "chat_completions",
        "prompt_version": "code-review-prompt-v1",
        "contract_version": "code-review-findings-v1",
        "reasoning_effort": "max",
        "max_output_tokens": 8000,
        "temperature": 0.0,
        "account_uid": "uid-primary",
        "account_alias": "primary",
        "subscription_id": "go-a",
        "catalog_version": "v",
        "catalog_effective_at": "2026-08-21T00:00:00+00:00",
        "catalog_source_url": "https://example.invalid",
        "created_at": "2026-08-22T00:00:00+00:00",
        "status": "queued",
        "usage_source": "local_estimate",
        "pricing_band": "pending",
        "file_count": 1,
        "changed_line_count": 1,
        "language": "python",
        "task_type": "general",
        "diff_size_bucket": "small",
        "artifact_relative_path": "jobs/1",
    }
    store.create_run(base)
    changed = dict(base, run_id="2", blind_label="review-22222222", diff_hash="c" * 64)
    with pytest.raises(RuntimeError, match="already bound"):
        store.create_run(changed)
    model_specific_policy = dict(
        base,
        run_id="3",
        blind_label="review-33333333",
        model="glm-5.3",
        reasoning_effort="high",
        max_output_tokens=16_384,
    )
    store.create_run(model_specific_policy)
    assert store.get_run("3")["max_output_tokens"] == 16_384


def test_backend_reports_model_specific_review_policies(tmp_path: Path) -> None:
    root, _base, _head = _repository(tmp_path)
    manager = CodeReviewManager(
        store=CodeReviewStore(tmp_path / "review-state"),
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})),
        account=OpenCodeAccount(uid="go-test-uid", alias="go-test"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
    )

    status = manager.backend_status(check_remote=False)
    policies = {
        item.model_id: (item.requested_reasoning_effort, item.max_output_tokens)
        for item in status.models
    }

    assert policies[CodeReviewModel.GLM_5_3] == ("max", 131_072)
    assert policies[CodeReviewModel.KIMI_K3] == ("max", 131_072)
    assert policies[CodeReviewModel.DEEPSEEK_V4_PRO] == ("max", 131_072)
    assert policies[CodeReviewModel.GROK_4_6] == ("max", 131_072)
    assert status.provider_timeout.policy_name == "remote_async_generation_v1"
    assert status.provider_timeout.read_seconds == 7_200
    assert status.patch_roots_configured is False
    assert status.patch_root_count == 0


def test_grok_review_uses_responses_protocol_and_normalizes_usage(tmp_path: Path) -> None:
    root, base, head = _repository(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENCODE_GO_RESPONSES_URL
        body = json.loads(request.content)
        assert body["model"] == CodeReviewModel.GROK_4_6.value
        assert body["reasoning"] == {"effort": "max"}
        assert body["max_output_tokens"] == 131_072
        assert body["text"]["format"]["strict"] is True
        assert "input" in body and "messages" not in body
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output_text": json.dumps(
                    {"findings": [], "omitted_context": [], "truncated": False}
                ),
                "usage": {
                    "input_tokens": 200,
                    "input_tokens_details": {"cached_tokens": 50},
                    "output_tokens": 40,
                    "output_tokens_details": {"reasoning_tokens": 20},
                },
            },
        )

    usage = UsageStore(tmp_path / "usage.db")
    manager = CodeReviewManager(
        store=CodeReviewStore(tmp_path / "review-state"),
        usage_store=usage,
        snapshotter=CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})),
        account=OpenCodeAccount(uid="go-test-uid", alias="go-test"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
    )
    submission = manager.submit(
        CodeReviewSubmitCommand(
            repository_id="sample",
            base_ref=base,
            head_ref=head,
            review_profile=CodeReviewProfile.GENERAL,
            model=CodeReviewModel.GROK_4_6,
        )
    )
    deadline = time.monotonic() + 5
    status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))
    while status.state in {CodeReviewJobState.QUEUED, CodeReviewJobState.RUNNING}:
        if time.monotonic() >= deadline:
            raise AssertionError("review job did not complete")
        time.sleep(0.02)
        status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))
    assert status.state is CodeReviewJobState.SUCCEEDED
    assert status.total_findings == 0
    summary = usage.summarize(days=30)
    assert summary.by_model[CodeReviewModel.GROK_4_6.value] == 1
    assert summary.prompt_cache_hit_tokens == 50
    assert summary.reasoning_tokens == 20


def test_grok_terminal_provider_status_maps_to_upstream_request_failure() -> None:
    assert CodeReviewManager._failure_kind(OpenCodeProviderResponseError("failed")) == (
        "PROVIDER_REQUEST_FAILED:UPSTREAM"
    )


def test_backend_reports_patch_root_presence_without_host_path(tmp_path: Path) -> None:
    root, _base, _head = _repository(tmp_path)
    patch_root = tmp_path / "dedicated-patches"
    patch_root.mkdir()
    manager = CodeReviewManager(
        store=CodeReviewStore(tmp_path / "review-state"),
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=CodeReviewSnapshotter(
            CodeReviewRepositoryCatalog({"sample": root}), patch_roots=(patch_root,)
        ),
        account=OpenCodeAccount(uid="go-test-uid", alias="go-test"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
    )

    status = manager.backend_status(check_remote=False)
    serialized = status.model_dump_json()

    assert status.patch_roots_configured is True
    assert status.patch_root_count == 1
    assert str(patch_root) not in serialized


def test_review_store_keeps_content_out_of_sqlite_and_versions_each_run(
    tmp_path: Path,
) -> None:
    import sqlite3

    store = CodeReviewStore(tmp_path / "state")
    with sqlite3.connect(store.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(code_review_runs)")}
    assert {
        "code_review_runs",
        "code_review_findings",
        "code_review_adjudications",
        "code_review_outcomes",
    }.issubset(tables)
    assert {"catalog_version", "catalog_effective_at", "catalog_source_url"}.issubset(run_columns)
    assert {"full_prompt", "full_diff", "source_text", "model_output"}.isdisjoint(run_columns)


def test_findings_must_overlap_a_changed_hunk(tmp_path: Path) -> None:
    root, base, head = _repository(tmp_path)
    snapshot = CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})).from_refs(
        "sample", base, head
    )
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "findings": [
                                {
                                    "finding_id": "unrelated_line",
                                    "category": "correctness",
                                    "severity": "low",
                                    "confidence": 0.8,
                                    "file": "app.py",
                                    "line_start": 500,
                                    "line_end": 500,
                                    "evidence_summary": "This is outside the supplied hunk.",
                                    "evidence_sha256": "0" * 64,
                                    "rationale": "It cannot be supported by the bounded input.",
                                    "suggested_validation_test": "Inspect the changed hunk.",
                                }
                            ],
                            "omitted_context": [],
                            "truncated": False,
                        }
                    )
                }
            }
        ]
    }
    with pytest.raises(ValueError, match="changed hunk"):
        CodeReviewManager._validated_payload(payload, snapshot)


def test_absolute_host_paths_are_deterministically_redacted_from_model_input(
    tmp_path: Path,
) -> None:
    root, _, head = _repository(tmp_path)
    base = head
    host_path = r"C:\Users\private-user\sensitive\input.txt"
    (root / "app.py").write_text(f'PATH = r"{host_path}"\n', encoding="utf-8")
    _git(root, "commit", "-am", "path")
    changed = _git(root, "rev-parse", "HEAD")
    snapshot = CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})).from_refs(
        "sample", base, changed
    )
    serialized = snapshot.diff_text + json.dumps(snapshot.context)
    assert host_path not in serialized
    assert "private-user" not in serialized
    assert "<HOST_PATH_" in serialized
    assert any("host absolute paths redacted" in item for item in snapshot.omitted_context)


def test_validation_failure_preserves_review_response_audit_and_usage(tmp_path: Path) -> None:
    root, base, head = _repository(tmp_path)
    usage = UsageStore(tmp_path / "usage.db")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"not_findings":true}'}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 5},
            },
        )

    store = CodeReviewStore(tmp_path / "review-state")
    manager = CodeReviewManager(
        store=store,
        usage_store=usage,
        snapshotter=CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})),
        account=OpenCodeAccount(uid="go-user@example.com", alias="go-user@example.com"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
    )
    submission = manager.submit(
        CodeReviewSubmitCommand(
            repository_id="sample",
            base_ref=base,
            head_ref=head,
            review_profile=CodeReviewProfile.GENERAL,
            model=CodeReviewModel.GLM_5_3,
        )
    )
    deadline = time.monotonic() + 5
    status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))
    while status.state in {CodeReviewJobState.QUEUED, CodeReviewJobState.RUNNING}:
        if time.monotonic() >= deadline:
            raise AssertionError("review job did not complete")
        time.sleep(0.02)
        status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))

    assert status.state is CodeReviewJobState.FAILED
    assert status.failure_code is CodeReviewFailureCode.INVALID_PROVIDER_RESPONSE
    assert "no retry was attempted" in status.detail
    audit = json.loads(
        (store.jobs_root / submission.job_id / "audit" / "provider-response.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["response_bytes"] > 0
    assert len(audit["response_sha256"]) == 64
    assert "choices" not in audit
    summary = usage.summarize(days=30)
    assert summary.total_calls == 1
    assert summary.failed_calls == 1
    assert summary.prompt_cache_miss_tokens == 30


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            {
                "choices": [{"finish_reason": "length", "message": {"content": "{"}}],
                "usage": {
                    "prompt_tokens": 21_111,
                    "completion_tokens": 8_000,
                    "completion_tokens_details": {"reasoning_tokens": 7_998},
                },
            },
            CodeReviewFailureCode.REASONING_BUDGET_EXHAUSTED,
        ),
        (
            {
                "choices": [{"finish_reason": "length", "message": {"content": "{"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 8_000,
                    "completion_tokens_details": {"reasoning_tokens": 1_000},
                },
            },
            CodeReviewFailureCode.OUTPUT_TRUNCATED,
        ),
        (
            {
                "choices": [{"finish_reason": "stop", "message": {"content": "not json"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
            CodeReviewFailureCode.INVALID_PROVIDER_RESPONSE,
        ),
    ],
)
def test_review_failure_codes_are_explicit_and_never_retry(
    tmp_path: Path,
    response: dict[str, object],
    expected: CodeReviewFailureCode,
) -> None:
    root, base, head = _repository(tmp_path)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=response)

    store = CodeReviewStore(tmp_path / "review-state")
    manager = CodeReviewManager(
        store=store,
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})),
        account=OpenCodeAccount(uid="go-user@example.com", alias="go-user@example.com"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
    )
    submission = manager.submit(
        CodeReviewSubmitCommand(
            repository_id="sample",
            base_ref=base,
            head_ref=head,
            review_profile=CodeReviewProfile.DATA_INTEGRITY,
            model=CodeReviewModel.GLM_5_3,
        )
    )
    deadline = time.monotonic() + 5
    status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))
    while status.state in {CodeReviewJobState.QUEUED, CodeReviewJobState.RUNNING}:
        if time.monotonic() >= deadline:
            raise AssertionError("review job did not complete")
        time.sleep(0.02)
        status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))

    assert calls == 1
    assert status.state is CodeReviewJobState.FAILED
    assert status.failure_code is expected
    assert "no retry was attempted" in status.detail
    assert status.artifacts
    assert all(item.relative_path.startswith("audit/") for item in status.artifacts)
    assert store.get_run(submission.job_id)["failure_kind"] == expected.value
    if expected is CodeReviewFailureCode.REASONING_BUDGET_EXHAUSTED:
        import sqlite3

        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "UPDATE code_review_runs SET failure_kind = 'ValueError' WHERE run_id = ?",
                (submission.job_id,),
            )
        legacy = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))
        assert legacy.failure_code is CodeReviewFailureCode.REASONING_BUDGET_EXHAUSTED


@pytest.mark.parametrize(
    ("content", "expected_stage"),
    [
        (
            "```json\n"
            + json.dumps({"findings": [], "omitted_context": [], "truncated": False})
            + "\n```",
            CodeReviewValidationStage.MARKDOWN_FENCE,
        ),
        ("PRIVATE_MODEL_OUTPUT not json", CodeReviewValidationStage.JSON_SYNTAX),
        (json.dumps({}), CodeReviewValidationStage.FINDINGS_SHAPE),
        (
            json.dumps({"findings": {}, "omitted_context": [], "truncated": False}),
            CodeReviewValidationStage.FINDINGS_SHAPE,
        ),
        (
            json.dumps({"findings": ["not-an-object"], "omitted_context": []}),
            CodeReviewValidationStage.FINDINGS_SHAPE,
        ),
        (None, CodeReviewValidationStage.CONTENT_MISSING_OR_OVERSIZED),
        (json.dumps([]), CodeReviewValidationStage.TOP_LEVEL_SHAPE),
    ],
    ids=[
        "fence",
        "json-syntax",
        "missing-findings",
        "findings-object",
        "finding-string",
        "missing-content",
        "top-level-array",
    ],
)
def test_review_validation_shape_stages_are_prompt_free_and_never_retry(
    tmp_path: Path,
    content: object,
    expected_stage: CodeReviewValidationStage,
) -> None:
    status, audit, calls, root = _run_review_fixture(tmp_path, content=content)

    assert calls == 1
    assert status.state is CodeReviewJobState.FAILED
    assert status.failure_code is CodeReviewFailureCode.INVALID_PROVIDER_RESPONSE
    assert status.validation_stage is expected_stage
    assert expected_stage.value in status.detail
    assert audit["validation_stage"] == expected_stage.value
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "PRIVATE_MODEL_OUTPUT" not in serialized
    assert "not-an-object" not in serialized
    assert "app.py" not in serialized
    assert str(root) not in serialized
    assert "choices" not in audit


def test_review_choice_shape_is_diagnosed_without_retry(tmp_path: Path) -> None:
    status, audit, calls, _ = _run_review_fixture(
        tmp_path,
        content=None,
        choices_override=[],
    )

    assert calls == 1
    assert status.failure_code is CodeReviewFailureCode.INVALID_PROVIDER_RESPONSE
    assert status.validation_stage is CodeReviewValidationStage.CHOICE_SHAPE
    assert audit["validation_stage"] == "CHOICE_SHAPE"


def test_review_explicit_empty_findings_succeeds_without_retry(tmp_path: Path) -> None:
    content = json.dumps({"findings": [], "omitted_context": [], "truncated": False})
    status, audit, calls, _ = _run_review_fixture(tmp_path, content=content)

    assert calls == 1
    assert status.state is CodeReviewJobState.SUCCEEDED
    assert status.validation_stage is None
    assert status.total_findings == 0
    assert status.progress_source == "provider_response"
    assert status.upstream_progress_confirmed is True
    assert status.usage_observed is True
    assert status.provider_timeout is not None
    assert status.provider_timeout.policy_name == "remote_async_generation_v1"
    assert status.provider_timeout.read_seconds == 7_200
    assert status.started_at is not None
    assert status.completed_at is not None
    assert status.latency_ms is not None
    assert status.latency_ms >= 0
    assert audit["findings_present"] is True
    assert audit["findings_type"] == "array"
    assert audit["finding_count"] == 0
    assert audit["validation_stage"] is None


def _schema_case(field: str) -> dict[str, object]:
    payload = _valid_review_payload()
    finding = payload["findings"][0]
    assert isinstance(finding, dict)
    if field == "finding_id":
        finding[field] = "X"
    elif field == "category":
        finding[field] = "unknown-category"
    elif field == "severity":
        finding[field] = "urgent"
    elif field == "confidence":
        finding[field] = 2
    elif field == "evidence_summary":
        finding[field] = "x" * 1_201
    elif field == "extra":
        finding["PRIVATE_EXTRA_FIELD"] = "PRIVATE_EXTRA_VALUE"
    elif field == "reversed_range":
        finding["line_start"] = 3
        finding["line_end"] = 2
    elif field == "wide_range":
        finding["line_start"] = 2
        finding["line_end"] = 83
    else:  # pragma: no cover - guarded by the parametrization
        raise AssertionError(field)
    return payload


@pytest.mark.parametrize(
    "field",
    [
        "finding_id",
        "category",
        "severity",
        "confidence",
        "evidence_summary",
        "extra",
        "reversed_range",
        "wide_range",
    ],
)
def test_review_finding_schema_diagnostics_are_value_free(tmp_path: Path, field: str) -> None:
    content = json.dumps(_schema_case(field))
    status, audit, calls, root = _run_review_fixture(tmp_path, content=content)

    assert calls == 1
    assert status.validation_stage is CodeReviewValidationStage.FINDING_SCHEMA
    assert audit["validation_stage"] == "FINDING_SCHEMA"
    assert audit["validation_issues"]
    assert all(set(item) == {"field_path", "error_type"} for item in audit["validation_issues"])
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "PRIVATE_EXTRA_FIELD" not in serialized
    assert "PRIVATE_EXTRA_VALUE" not in serialized
    assert "Bounded evidence marker" not in serialized
    assert "Bounded rationale marker" not in serialized
    assert "app.py" not in serialized
    assert str(root) not in serialized


@pytest.mark.parametrize(
    ("case", "expected_stage"),
    [
        ("duplicate", CodeReviewValidationStage.DUPLICATE_ID),
        ("file", CodeReviewValidationStage.FILE_SCOPE),
        ("hunk", CodeReviewValidationStage.HUNK_SCOPE),
    ],
)
def test_review_scope_stages_are_explicit_and_never_retry(
    tmp_path: Path, case: str, expected_stage: CodeReviewValidationStage
) -> None:
    payload = _valid_review_payload()
    finding = payload["findings"][0]
    assert isinstance(finding, dict)
    if case == "duplicate":
        payload["findings"].append(dict(finding))
    elif case == "file":
        finding["file"] = "other.py"
    else:
        finding["line_start"] = 100
        finding["line_end"] = 100

    status, audit, calls, _ = _run_review_fixture(tmp_path, content=json.dumps(payload))

    assert calls == 1
    assert status.failure_code is CodeReviewFailureCode.INVALID_PROVIDER_RESPONSE
    assert status.validation_stage is expected_stage
    assert audit["validation_stage"] == expected_stage.value


def test_review_stop_with_reasoning_heavy_usage_is_not_misclassified(tmp_path: Path) -> None:
    status, audit, calls, _ = _run_review_fixture(
        tmp_path,
        content="not json",
        usage={
            "prompt_tokens": 21_111,
            "completion_tokens": 6_892,
            "completion_tokens_details": {"reasoning_tokens": 6_349},
        },
    )

    assert calls == 1
    assert status.failure_code is CodeReviewFailureCode.INVALID_PROVIDER_RESPONSE
    assert status.validation_stage is CodeReviewValidationStage.JSON_SYNTAX
    assert audit["visible_completion_tokens"] == 543
    assert audit["reasoning_ratio"] == round(6_349 / 6_892, 6)


def _synthetic_review_snapshot(diff_payload_bytes: int) -> CodeReviewSnapshot:
    header = "diff --git a/src/large.py b/src/large.py\n"
    hunk = "@@ -1,1 +1,1 @@\n-old\n+new\n"
    padding = "+" + ("x" * max(1, diff_payload_bytes - len(header) - len(hunk) - 2)) + "\n"
    diff_text = header + hunk + padding
    digest = hashlib.sha256(diff_text.encode()).hexdigest()
    return CodeReviewSnapshot(
        repository_id="sample",
        diff_text=diff_text,
        diff_sha256=digest,
        snapshot_sha256="1" * 64,
        changed_files=("src/large.py",),
        changed_line_count=3,
        language="Python",
        context=(),
        omitted_context=(),
        source_identity={"base_commit": "2" * 40, "head_commit": "3" * 40},
    )


def test_review_preflight_uses_context_boundary_not_old_reasoning_caps() -> None:
    moderate = _synthetic_review_snapshot(150_000)
    oversized = _synthetic_review_snapshot(2_600_000)
    for model in CodeReviewModel:
        command = CodeReviewSubmitCommand(
            repository_id="sample",
            base_ref="2" * 40,
            head_ref="3" * 40,
            review_profile=CodeReviewProfile.DATA_INTEGRITY,
            model=model,
        )
        assert CodeReviewManager._preflight_partition_plan(command, moderate) is None
        plan = CodeReviewManager._preflight_partition_plan(command, oversized)
        assert plan is not None
        assert plan.max_output_tokens == 131_072
        assert plan.context_tokens == (500_000 if model is CodeReviewModel.GROK_4_6 else 1_000_000)
        assert plan.advisory_only is True
        assert plan.shards[0].oversized_single_file is True


def test_review_partition_preflight_creates_failed_job_without_provider_call(
    tmp_path: Path,
) -> None:
    snapshot = _synthetic_review_snapshot(2_600_000)
    calls = 0

    class FixedSnapshotter:
        def from_refs(self, *_args: object) -> CodeReviewSnapshot:
            return snapshot

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("provider must not be called for partition-required input")

    manager = CodeReviewManager(
        store=CodeReviewStore(tmp_path / "review-state"),
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=FixedSnapshotter(),  # type: ignore[arg-type]
        account=OpenCodeAccount(uid="go-test-uid", alias="go-test"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
    )
    submission = manager.submit(
        CodeReviewSubmitCommand(
            repository_id="sample",
            base_ref="2" * 40,
            head_ref="3" * 40,
            review_profile=CodeReviewProfile.DATA_INTEGRITY,
            model=CodeReviewModel.DEEPSEEK_V4_PRO,
        )
    )
    status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))

    assert calls == 0
    assert submission.state is CodeReviewJobState.FAILED
    assert submission.failure_code is CodeReviewFailureCode.REVIEW_PARTITION_REQUIRED
    assert status.failure_code is CodeReviewFailureCode.REVIEW_PARTITION_REQUIRED
    assert status.partition_plan == submission.partition_plan
    assert status.partition_plan is not None
    assert all(item.relative_path.startswith("audit/") for item in status.artifacts)


@pytest.mark.parametrize(
    ("response_status", "request_error", "expected"),
    [
        (401, None, CodeReviewProviderFailureClass.AUTH),
        (429, None, CodeReviewProviderFailureClass.RATE_LIMIT),
        (503, None, CodeReviewProviderFailureClass.UPSTREAM),
        (418, None, CodeReviewProviderFailureClass.UNKNOWN),
        (None, "timeout", CodeReviewProviderFailureClass.TIMEOUT),
        (None, "transport", CodeReviewProviderFailureClass.TRANSPORT),
    ],
)
def test_review_provider_failures_are_safely_classified_without_retry(
    tmp_path: Path,
    response_status: int | None,
    request_error: str | None,
    expected: CodeReviewProviderFailureClass,
) -> None:
    root, base, head = _repository(tmp_path)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request_error == "timeout":
            raise httpx.ReadTimeout("PRIVATE_TIMEOUT_TEXT", request=request)
        if request_error == "transport":
            raise httpx.ConnectError("PRIVATE_TRANSPORT_TEXT", request=request)
        return httpx.Response(
            response_status or 500,
            json={"error": {"message": "PRIVATE_PROVIDER_BODY", "type": "PRIVATE_TYPE"}},
        )

    store = CodeReviewStore(tmp_path / "review-state")
    manager = CodeReviewManager(
        store=store,
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})),
        account=OpenCodeAccount(uid="go-test-uid", alias="go-test"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
    )
    submission = manager.submit(
        CodeReviewSubmitCommand(
            repository_id="sample",
            base_ref=base,
            head_ref=head,
            review_profile=CodeReviewProfile.GENERAL,
            model=CodeReviewModel.GLM_5_3,
        )
    )
    deadline = time.monotonic() + 5
    status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))
    while status.state in {CodeReviewJobState.QUEUED, CodeReviewJobState.RUNNING}:
        if time.monotonic() >= deadline:
            raise AssertionError("review job did not complete")
        time.sleep(0.02)
        status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))

    audit_text = (store.jobs_root / submission.job_id / "audit" / "provider-error.json").read_text(
        encoding="utf-8"
    )
    assert calls == 1
    assert status.failure_code is CodeReviewFailureCode.PROVIDER_REQUEST_FAILED
    assert status.provider_failure_class is expected
    assert expected.value in audit_text
    assert "PRIVATE_" not in audit_text
    assert status.usage_observed is False
    assert status.upstream_progress_confirmed is False
    if request_error == "timeout":
        audit = json.loads(audit_text)
        assert status.timeout_phase == "read"
        assert audit["timeout_phase"] == "read"
        assert audit["timeout_policy"]["read_seconds"] == 7_200
        assert audit["usage_observed"] is False


def test_review_remote_protocol_failure_retains_encrypted_partial_wire_by_job_uid(
    tmp_path: Path,
) -> None:
    root, base, head = _repository(tmp_path)
    calls = 0
    key = b"w" * 32

    class FailingStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"choices":[{"message":{"content":"partial'
            raise httpx.RemoteProtocolError("PRIVATE_STREAM_FAILURE")

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=FailingStream())

    store = CodeReviewStore(tmp_path / "review-state")
    manager = CodeReviewManager(
        store=store,
        usage_store=UsageStore(tmp_path / "usage.db"),
        snapshotter=CodeReviewSnapshotter(CodeReviewRepositoryCatalog({"sample": root})),
        account=OpenCodeAccount(uid="go-test-uid", alias="go-test"),
        api_key_provider=lambda: "opaque-test-key-1234567890",
        transport=httpx.MockTransport(handler),
        encrypted_wire_capture=True,
        wire_capture_key_provider=lambda: key,
        wire_capture_max_bytes_override=1024 * 1024,
    )
    submission = manager.submit(
        CodeReviewSubmitCommand(
            repository_id="sample",
            base_ref=base,
            head_ref=head,
            review_profile=CodeReviewProfile.GENERAL,
            model=CodeReviewModel.KIMI_K3,
        )
    )
    deadline = time.monotonic() + 5
    status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))
    while status.state in {CodeReviewJobState.QUEUED, CodeReviewJobState.RUNNING}:
        if time.monotonic() >= deadline:
            raise AssertionError("review job did not complete")
        time.sleep(0.02)
        status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))

    job_root = store.jobs_root / submission.job_id
    wire_audit_text = (job_root / "audit" / "wire-capture.json").read_text("utf-8")
    provider_audit_text = (job_root / "audit" / "provider-error.json").read_text("utf-8")
    request_plaintext = decrypt_wire_file(
        job_root / "wire" / "request.wire",
        job_id=submission.job_id,
        direction="request",
        key=key,
    )
    response_plaintext = decrypt_wire_file(
        job_root / "wire" / "response.wire.partial",
        job_id=submission.job_id,
        direction="response",
        key=key,
    )
    assert calls == 1
    assert status.transport_failure_kind == "REMOTE_PROTOCOL"
    assert status.wire_capture_uid == submission.job_id
    assert response_plaintext.endswith(b'"partial')
    assert b"opaque-test-key" not in request_plaintext
    assert submission.job_id in wire_audit_text
    assert submission.job_id in provider_audit_text
    assert "PRIVATE_STREAM_FAILURE" not in wire_audit_text
    assert "PRIVATE_STREAM_FAILURE" not in provider_audit_text

    observed = CodeReviewExternalUsageCommand(
        job_id=submission.job_id,
        observed_at=datetime.fromisoformat("2026-08-28T15:20:00+08:00"),
        input_tokens=50_110,
        output_tokens=12_141,
        provider_reported_cost_usd=0.3324,
    )
    receipt = manager.reconcile_external_usage(observed)
    replay = manager.reconcile_external_usage(observed)
    assert receipt.attribution_uid == submission.job_id
    assert receipt.idempotent_replay is False
    assert replay.api_usage_id == receipt.api_usage_id
    assert replay.idempotent_replay is True
    with pytest.raises(RuntimeError, match="conflicts"):
        manager.reconcile_external_usage(
            observed.model_copy(update={"provider_reported_cost_usd": 0.3325})
        )
    reconciled = store.get_usage_reconciliation(submission.job_id)
    assert reconciled is not None
    assert reconciled["input_tokens"] == 50_110
    assert reconciled["output_tokens"] == 12_141
    assert reconciled["provider_reported_cost_usd"] == pytest.approx(0.3324)
    reconciled_status = manager.status(CodeReviewStatusCommand(job_id=submission.job_id))
    assert reconciled_status.progress_source == "provider_dashboard"
    assert reconciled_status.usage_observed is True
    assert reconciled_status.usage_observation_scope == "provider_dashboard_totals"
    assert reconciled_status.input_tokens == 50_110
    assert reconciled_status.output_tokens == 12_141
    assert reconciled_status.provider_reported_cost_usd == pytest.approx(0.3324)


def test_review_two_noncanonical_categories_fail_without_value_leak_or_retry(
    tmp_path: Path,
) -> None:
    payload = _valid_review_payload()
    first = payload["findings"][0]
    assert isinstance(first, dict)
    first["category"] = "PRIVATE_CATEGORY_ALPHA"
    second = dict(first)
    second["finding_id"] = "second_finding"
    second["category"] = "PRIVATE_CATEGORY_BETA"
    third = dict(first)
    third["finding_id"] = "third_finding"
    third["category"] = "testing"
    payload["findings"] = [first, second, third]

    status, audit, calls, _ = _run_review_fixture(tmp_path, content=json.dumps(payload))

    assert calls == 1
    assert status.failure_code is CodeReviewFailureCode.INVALID_PROVIDER_RESPONSE
    assert status.validation_stage is CodeReviewValidationStage.FINDING_SCHEMA
    assert audit["validation_stage"] == "FINDING_SCHEMA"
    assert audit["validation_issues"] == [
        {"field_path": "findings.0.category", "error_type": "enum"},
        {"field_path": "findings.1.category", "error_type": "enum"},
    ]
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "PRIVATE_CATEGORY_ALPHA" not in serialized
    assert "PRIVATE_CATEGORY_BETA" not in serialized
    assert "testing" not in serialized


def test_review_three_canonical_categories_succeed_without_retry(tmp_path: Path) -> None:
    payload = _valid_review_payload()
    first = payload["findings"][0]
    assert isinstance(first, dict)
    categories = ("correctness", "security", "testing")
    findings = []
    for index, category in enumerate(categories, start=1):
        finding = dict(first)
        finding["finding_id"] = f"canonical_{index}"
        finding["category"] = category
        findings.append(finding)
    payload["findings"] = findings

    status, audit, calls, _ = _run_review_fixture(tmp_path, content=json.dumps(payload))

    assert calls == 1
    assert status.state is CodeReviewJobState.SUCCEEDED
    assert status.failure_code is None
    assert status.validation_stage is None
    assert status.total_findings == 3
    assert [finding.category.value for finding in status.findings] == list(categories)
    assert audit["finding_count"] == 3
    assert audit["validation_stage"] is None


@pytest.mark.parametrize("finding_file", ["src/app.py", "src\\app.py"])
def test_review_exact_or_windows_normalized_changed_file_succeeds_without_retry(
    tmp_path: Path, finding_file: str
) -> None:
    payload = _valid_review_payload()
    finding = payload["findings"][0]
    assert isinstance(finding, dict)
    finding["file"] = finding_file

    status, audit, calls, _ = _run_review_fixture(
        tmp_path,
        content=json.dumps(payload),
        repository_file="src/app.py",
    )

    assert calls == 1
    assert status.state is CodeReviewJobState.SUCCEEDED
    assert status.findings[0].file == "src/app.py"
    assert audit["validation_stage"] is None
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "src/app.py" not in serialized
    assert "src\\\\app.py" not in serialized


@pytest.mark.parametrize(
    ("finding_file", "expected_stage"),
    [
        ("other.py", CodeReviewValidationStage.FILE_SCOPE),
        ("a/src/app.py", CodeReviewValidationStage.FILE_SCOPE),
        ("b/src/app.py", CodeReviewValidationStage.FILE_SCOPE),
        ("src/app.py.extra", CodeReviewValidationStage.FILE_SCOPE),
        ("src/App.py", CodeReviewValidationStage.FILE_SCOPE),
        ("../src/app.py", CodeReviewValidationStage.FINDING_SCHEMA),
        ("src/../src/app.py", CodeReviewValidationStage.FINDING_SCHEMA),
    ],
    ids=[
        "outside",
        "git-a-prefix",
        "git-b-prefix",
        "similar-prefix",
        "case-change",
        "parent-prefix",
        "parent-middle",
    ],
)
def test_review_changed_file_boundary_rejects_without_path_leak_or_retry(
    tmp_path: Path,
    finding_file: str,
    expected_stage: CodeReviewValidationStage,
) -> None:
    payload = _valid_review_payload()
    finding = payload["findings"][0]
    assert isinstance(finding, dict)
    finding["file"] = finding_file

    status, audit, calls, root = _run_review_fixture(
        tmp_path,
        content=json.dumps(payload),
        repository_file="src/app.py",
    )

    assert calls == 1
    assert status.failure_code is CodeReviewFailureCode.INVALID_PROVIDER_RESPONSE
    assert status.validation_stage is expected_stage
    assert audit["validation_stage"] == expected_stage.value
    serialized = json.dumps(audit, ensure_ascii=False)
    assert finding_file not in serialized
    assert "src/app.py" not in serialized
    assert str(root) not in serialized
