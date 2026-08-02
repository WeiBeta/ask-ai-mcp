"""Tests for the opt-in billed lifecycle smoke command."""

from __future__ import annotations

from types import SimpleNamespace

from ask_ai_mcp import lifecycle_cli
from ask_ai_mcp.models import CandidateLifecycleStatus


def test_charge_confirmation_is_required(capsys) -> None:
    assert lifecycle_cli.main([]) == 2
    assert "--confirm-charge" in capsys.readouterr().err


def test_unavailable_runner_prevents_api_call(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        lifecycle_cli,
        "docker_backend_status",
        lambda: SimpleNamespace(ready=False, reasons=["runner_image_not_available"]),
    )
    monkeypatch.setattr(
        lifecycle_cli,
        "CandidateLifecycle",
        lambda: (_ for _ in ()).throw(AssertionError("must not call DeepSeek")),
    )
    assert lifecycle_cli.main(["--confirm-charge"]) == 2
    assert "runner_image_not_available" in capsys.readouterr().err


def test_success_prints_only_content_free_identifiers(monkeypatch, capsys) -> None:
    review = SimpleNamespace(
        job_id="52efb642-6d4a-42ea-9bbf-da5197360c77",
        candidate_sha256="a" * 64,
        attempts=[1],
        execution=SimpleNamespace(tests_run=2),
    )
    result = SimpleNamespace(status=CandidateLifecycleStatus.REVIEW_PENDING, review=review)

    class FakeLifecycle:
        def run(self, *_args, **_kwargs):
            return result

    monkeypatch.setattr(
        lifecycle_cli,
        "docker_backend_status",
        lambda: SimpleNamespace(ready=True, reasons=[]),
    )
    monkeypatch.setattr(lifecycle_cli, "CandidateLifecycle", FakeLifecycle)
    assert lifecycle_cli.main(["--confirm-charge"]) == 0
    output = capsys.readouterr().out
    assert review.job_id in output
    assert review.candidate_sha256 in output
    assert "source was not printed" in output
