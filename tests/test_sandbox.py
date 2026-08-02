"""Tests for Docker Desktop WSL 2 backend readiness checks."""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ask_ai_mcp import sandbox
from ask_ai_mcp.models import CandidateJobManifest, CandidateJobState


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
            return SimpleNamespace(returncode=0, stdout="linux\n")
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
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="linux\n"),
    )
    assert sandbox.docker_backend_status().ready is True


def test_windows_container_engine_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "find_docker_cli", lambda: Path("docker.exe"))
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="windows\n"),
    )
    status = sandbox.docker_backend_status()
    assert status.ready is False
    assert status.reasons == ["docker_engine_not_linux"]


def test_executor_requires_digest_pinned_image() -> None:
    with pytest.raises(ValueError, match="content digest"):
        sandbox.DockerCandidateExecutor(docker_cli=Path("docker.exe"), runner_image="python:latest")


def make_job(tmp_path: Path) -> Path:
    job_id = str(uuid4())
    root = tmp_path / job_id
    for name in ("candidate", "input", "output", "control"):
        (root / name).mkdir(parents=True)
    candidate = root / "candidate" / "test_tool.py"
    candidate.write_text(
        "import unittest\n\nclass TestTool(unittest.TestCase):\n"
        "    def test_true(self):\n        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    import hashlib

    manifest = CandidateJobManifest(
        job_id=job_id,
        state=CandidateJobState.STATIC_APPROVED,
        tool_name="test_tool",
        spec_sha256="a" * 64,
        candidate_sha256="b" * 64,
        candidate_files=["test_tool.py"],
        candidate_file_sha256={"test_tool.py": hashlib.sha256(candidate.read_bytes()).hexdigest()},
    )
    (root / "control" / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return root


def test_executor_builds_hardened_docker_command(tmp_path: Path, monkeypatch) -> None:
    root = make_job(tmp_path)
    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            import io

            captured["command"] = command
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(b"Ran 1 test in 0.001s\nOK\n")

        def wait(self, timeout):
            captured["timeout"] = timeout
            return 0

    monkeypatch.setattr(sandbox.subprocess, "Popen", FakeProcess)
    executor = sandbox.DockerCandidateExecutor(docker_cli=Path("docker.exe"))
    report = executor.execute(root)

    command = captured["command"]
    assert "--network" in command and "none" in command
    assert "--read-only" in command
    assert command[command.index("--cap-drop") : command.index("--cap-drop") + 2] == [
        "--cap-drop",
        "ALL",
    ]
    assert "no-new-privileges:true" in command
    assert "65532:65532" in command
    assert "--pids-limit" in command
    assert "nofile=256:256" in command
    assert "core=0:0" in command
    assert "--memory" in command
    assert "--cpus" in command
    assert sandbox.DEFAULT_RUNNER_IMAGE in command
    assert report.state is CandidateJobState.EXECUTED
    assert report.tests_run == 1
    persisted = json.loads((root / "control" / "execution.json").read_text(encoding="utf-8"))
    assert persisted["runner_image"] == sandbox.DEFAULT_RUNNER_IMAGE


def test_executor_rejects_candidate_changed_after_approval(tmp_path: Path) -> None:
    root = make_job(tmp_path)
    (root / "candidate" / "test_tool.py").write_text("changed = True\n", encoding="utf-8")
    executor = sandbox.DockerCandidateExecutor(docker_cli=Path("docker.exe"))
    with pytest.raises(sandbox.CandidateExecutionError, match="changed"):
        executor.execute(root)


def test_executor_rejects_nonempty_output_directory(tmp_path: Path) -> None:
    root = make_job(tmp_path)
    (root / "output" / "existing.txt").write_text("keep", encoding="utf-8")
    executor = sandbox.DockerCandidateExecutor(docker_cli=Path("docker.exe"))
    with pytest.raises(sandbox.CandidateExecutionError, match="must be empty"):
        executor.execute(root)


def test_executor_caps_output_and_cleans_up_on_timeout(tmp_path: Path, monkeypatch) -> None:
    import io
    import subprocess

    root = make_job(tmp_path)
    removed: list[str] = []

    class TimedOutProcess:
        stdout = io.BytesIO(b"x" * 8192)
        stderr = io.BytesIO(b"")

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("docker", timeout)

        def kill(self):
            return None

    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *_args, **_kwargs: TimedOutProcess())
    executor = sandbox.DockerCandidateExecutor(
        docker_cli=Path("docker.exe"),
        limits=sandbox.SandboxLimits(timeout_seconds=1, max_output_bytes=4096),
    )
    monkeypatch.setattr(executor, "_remove_container", removed.append)

    report = executor.execute(root)

    assert report.state is CandidateJobState.EXECUTION_FAILED
    assert report.timed_out is True
    assert report.exit_code is None
    assert report.output_truncated is True
    assert len(report.stdout.encode("utf-8")) == 4096
    assert removed == [f"ask-ai-mcp-{root.name}"]
