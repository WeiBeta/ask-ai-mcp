"""Tests for Docker Desktop WSL 2 backend readiness checks."""

from pathlib import Path
from types import SimpleNamespace

from ask_ai_mcp import sandbox


def test_missing_docker_cli_is_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "find_docker_cli", lambda: None)
    status = sandbox.docker_backend_status()
    assert status.ready is False
    assert status.reasons == ["docker_cli_not_available"]


def test_engine_and_image_are_required(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[1] == "version":
            return SimpleNamespace(returncode=0, stdout="28.0.0\n")
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(sandbox, "find_docker_cli", lambda: Path("docker.exe"))
    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    status = sandbox.docker_backend_status()
    assert status.engine_available is True
    assert status.image_available is False
    assert status.reasons == ["runner_image_not_available"]
    assert len(calls) == 2


def test_ready_when_engine_and_image_are_available(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "find_docker_cli", lambda: Path("docker.exe"))
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="ready\n"),
    )
    assert sandbox.docker_backend_status().ready is True
