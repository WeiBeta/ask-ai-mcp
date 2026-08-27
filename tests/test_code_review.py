"""Tests for the isolated, fixed-model code-review MCP module."""

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
from ask_ai_mcp.code_review import OPENCODE_GO_CHAT_URL, CodeReviewManager
from ask_ai_mcp.code_review_models import (
    CodeReviewAdjudicationCommand,
    CodeReviewAdjudicationDecision,
    CodeReviewJobState,
    CodeReviewModel,
    CodeReviewProfile,
    CodeReviewStatusCommand,
    CodeReviewSubmitCommand,
)
from ask_ai_mcp.code_review_store import CodeReviewStore
from ask_ai_mcp.code_review_workspace import (
    CodeReviewRepositoryCatalog,
    CodeReviewSnapshotter,
    CodeReviewWorkspaceError,
)
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


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Ask AI Tests")
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "app.py").write_text(
        "def add(a, b):\n    if a is None:\n        return 0\n    return a + b\n",
        encoding="utf-8",
    )
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "change")
    head = _git(root, "rev-parse", "HEAD")
    return root, base, head


def test_review_mcp_is_a_three_tool_surface_excluded_from_full() -> None:
    review_tools = {tool.name for tool in asyncio.run(server.review_mcp.list_tools())}
    full_tools = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert review_tools == {
        "code_review_backend_status",
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
    patch_root = tmp_path / "patches"
    patch_root.mkdir()
    snapshotter = CodeReviewSnapshotter(
        CodeReviewRepositoryCatalog({"sample": root}), patch_roots=(patch_root,)
    )
    traversal = b"diff --git a/../secret.py b/../secret.py\n+print('x')\n"
    traversal_path = patch_root / "traversal.patch"
    traversal_path.write_bytes(traversal)
    with pytest.raises(CodeReviewWorkspaceError, match="relative path"):
        snapshotter.from_patch("sample", str(traversal_path), hashlib.sha256(traversal).hexdigest())

    secret = (
        b"diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n+API_KEY='abcdefghijklmnopqrstuvwxyz'\n"
    )
    secret_path = patch_root / "secret.patch"
    secret_path.write_bytes(secret)
    with pytest.raises(CodeReviewWorkspaceError, match="no reviewable"):
        snapshotter.from_patch("sample", str(secret_path), hashlib.sha256(secret).hexdigest())


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
        prompt = body["messages"][1]["content"]
        seen_prompts.append(prompt)
        assert str(root) not in prompt
        assert "app.py" in prompt
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
