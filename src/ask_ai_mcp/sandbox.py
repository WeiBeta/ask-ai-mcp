"""Hardened Docker Desktop WSL 2 candidate execution backend."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from ask_ai_mcp.models import (
    CandidateExecutionReport,
    CandidateJobManifest,
    CandidateJobState,
    SandboxBackendStatus,
    VerifiedToolContainerReport,
)

DEFAULT_RUNNER_IMAGE = (
    "python@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64"
)
SYSTEM_DOCKER_CLI = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
BACKEND_NAME = "docker_desktop_wsl2"
_TEST_COUNT = re.compile(r"Ran (\d+) tests? in")
_PINNED_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$")
_CONTAINER_HARNESS = """
import pathlib
import runpy
import sys
import unittest

root = pathlib.Path("/workspace/candidate")
for path in sorted(root.rglob("*.py")):
    compile(path.read_bytes(), str(path), "exec")
suite = unittest.defaultTestLoader.discover(str(root), pattern="test*.py")
count = suite.countTestCases()
if count == 0:
    print("No unittest-compatible tests were discovered.", file=sys.stderr)
    raise SystemExit(3)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
entrypoint = sys.argv[1]
if entrypoint:
    namespace = runpy.run_path(str(root / entrypoint))
    if not callable(namespace.get("run")):
        print("Entrypoint does not define callable run().", file=sys.stderr)
        raise SystemExit(4)
raise SystemExit(0)
""".strip()

_VERIFIED_TOOL_HARNESS = """
import json
import pathlib
import runpy
import sys

candidate_root = pathlib.Path("/workspace/candidate")
input_root = pathlib.Path("/workspace/input")
output_root = pathlib.Path("/workspace/output")
entrypoint = pathlib.PurePosixPath(sys.argv[1])
if entrypoint.is_absolute() or ".." in entrypoint.parts:
    raise SystemExit("Invalid registered entrypoint")
namespace = runpy.run_path(str(candidate_root.joinpath(*entrypoint.parts)))
runner = namespace.get("run")
if not callable(runner):
    raise SystemExit("Registered entrypoint does not define callable run()")
request = json.loads((input_root / "request.json").read_text(encoding="utf-8"))
result = runner(request, input_root / "files", output_root)
serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
if len(serialized.encode("utf-8")) > 1048576:
    raise SystemExit("Tool result JSON exceeds 1 MiB")
(output_root / "_ask_ai_result.json").write_text(serialized, encoding="utf-8")
""".strip()


class CandidateExecutionError(RuntimeError):
    """Raised when a staged job is not eligible for isolated execution."""


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: int = 30
    memory_mb: int = 256
    cpus: float = 1.0
    pids: int = 64
    max_output_bytes: int = 65_536

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 1 and 300")
        if not 64 <= self.memory_mb <= 2048:
            raise ValueError("memory_mb must be between 64 and 2048")
        if not 0.25 <= self.cpus <= 4:
            raise ValueError("cpus must be between 0.25 and 4")
        if not 16 <= self.pids <= 256:
            raise ValueError("pids must be between 16 and 256")
        if not 4_096 <= self.max_output_bytes <= 65_536:
            raise ValueError("max_output_bytes must be between 4096 and 65536")


class _BoundedCapture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def drain(self, stream) -> None:
        while chunk := stream.read(8192):
            remaining = self.limit - len(self.data)
            if remaining > 0:
                self.data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self.truncated = True

    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


def find_docker_cli() -> Path | None:
    discovered = shutil.which("docker.exe") or shutil.which("docker")
    if discovered:
        return Path(discovered)
    if SYSTEM_DOCKER_CLI.is_file():
        return SYSTEM_DOCKER_CLI
    return None


def docker_backend_status() -> SandboxBackendStatus:
    docker_cli = find_docker_cli()
    reasons: list[str] = []
    engine_available = False
    image_available = False

    if docker_cli is None:
        reasons.append("docker_cli_not_available")
    else:
        try:
            engine = subprocess.run(
                [str(docker_cli), "version", "--format", "{{.Server.Os}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            engine_available = engine.returncode == 0 and bool(engine.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            engine_available = False
        if not engine_available:
            reasons.append("docker_engine_not_available")
        elif engine.stdout.strip() != "linux":
            reasons.append("docker_engine_not_linux")
        else:
            image = subprocess.run(
                [
                    str(docker_cli),
                    "image",
                    "inspect",
                    DEFAULT_RUNNER_IMAGE,
                    "--format",
                    "{{.Os}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            image_available = image.returncode == 0 and image.stdout.strip() == "linux"
            if not image_available:
                reasons.append("runner_image_not_available")

    return SandboxBackendStatus(
        backend=BACKEND_NAME,
        ready=not reasons,
        container_cli_available=docker_cli is not None,
        engine_available=engine_available,
        image_available=image_available,
        reasons=reasons,
    )


class DockerCandidateExecutor:
    """Execute one statically approved job inside a disposable Linux container."""

    def __init__(
        self,
        *,
        docker_cli: Path | None = None,
        runner_image: str = DEFAULT_RUNNER_IMAGE,
        limits: SandboxLimits | None = None,
    ) -> None:
        self.docker_cli = docker_cli or find_docker_cli()
        if not _PINNED_IMAGE.fullmatch(runner_image):
            raise ValueError("runner_image must use a sha256 content digest")
        self.runner_image = runner_image
        self.limits = limits or SandboxLimits()

    def execute(self, job_root: Path) -> CandidateExecutionReport:
        root = job_root.resolve(strict=True)
        manifest_path = root / "control" / "manifest.json"
        manifest = CandidateJobManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        if manifest.state is not CandidateJobState.STATIC_APPROVED:
            raise CandidateExecutionError("job is not in static-approved state")
        if UUID(manifest.job_id).version != 4 or root.name != manifest.job_id:
            raise CandidateExecutionError("job identity does not match its workspace")
        if self.docker_cli is None:
            raise CandidateExecutionError("Docker CLI is not available")

        candidate_root = self._require_plain_directory(root, "candidate")
        input_root = self._require_plain_directory(root, "input")
        output_root = self._require_plain_directory(root, "output")
        self._verify_candidate_files(candidate_root, manifest)
        if any(output_root.iterdir()):
            raise CandidateExecutionError("job output directory must be empty before execution")

        container_name = f"ask-ai-mcp-{manifest.job_id}"
        command = self._docker_command(
            container_name=container_name,
            candidate_root=candidate_root,
            input_root=input_root,
            output_root=output_root,
            entrypoint=manifest.entrypoint or "",
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = _BoundedCapture(self.limits.max_output_bytes)
        stderr = _BoundedCapture(self.limits.max_output_bytes)
        threads = [
            threading.Thread(target=stdout.drain, args=(process.stdout,), daemon=True),
            threading.Thread(target=stderr.drain, args=(process.stderr,), daemon=True),
        ]
        for thread in threads:
            thread.start()

        timed_out = False
        try:
            exit_code = process.wait(timeout=self.limits.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._remove_container(container_name)
            process.kill()
            exit_code = None
        finally:
            for thread in threads:
                thread.join(timeout=5)

        stderr_text = stderr.text()
        match = _TEST_COUNT.search(stderr_text)
        tests_run = int(match.group(1)) if match else 0
        state = (
            CandidateJobState.EXECUTED
            if exit_code == 0 and not timed_out
            else CandidateJobState.EXECUTION_FAILED
        )
        report = CandidateExecutionReport(
            job_id=manifest.job_id,
            candidate_sha256=manifest.candidate_sha256,
            state=state,
            backend=BACKEND_NAME,
            runner_image=self.runner_image,
            exit_code=exit_code,
            timed_out=timed_out,
            tests_run=tests_run,
            stdout=stdout.text(),
            stderr=stderr_text,
            output_truncated=stdout.truncated or stderr.truncated,
        )
        self._write_control_file(
            root / "control" / "execution.json", report.model_dump_json(indent=2)
        )
        updated_manifest = manifest.model_copy(
            update={"state": state, "execution_backend": BACKEND_NAME}
        )
        self._write_control_file(manifest_path, updated_manifest.model_dump_json(indent=2))
        return report

    def _docker_command(
        self,
        *,
        container_name: str,
        candidate_root: Path,
        input_root: Path,
        output_root: Path,
        entrypoint: str,
    ) -> list[str]:
        assert self.docker_cli is not None
        return [
            str(self.docker_cli),
            "run",
            "--rm",
            "--name",
            container_name,
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "65532:65532",
            "--pids-limit",
            str(self.limits.pids),
            "--ulimit",
            "nofile=256:256",
            "--ulimit",
            "core=0:0",
            "--memory",
            f"{self.limits.memory_mb}m",
            "--cpus",
            str(self.limits.cpus),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--mount",
            self._bind_mount(candidate_root, "/workspace/candidate", read_only=True),
            "--mount",
            self._bind_mount(input_root, "/workspace/input", read_only=True),
            "--mount",
            self._bind_mount(output_root, "/workspace/output", read_only=False),
            "--workdir",
            "/workspace/candidate",
            self.runner_image,
            "python",
            "-B",
            "-c",
            _CONTAINER_HARNESS,
            entrypoint,
        ]

    @staticmethod
    def _bind_mount(source: Path, target: str, *, read_only: bool) -> str:
        options = ["type=bind", f"src={source}", f"dst={target}"]
        if read_only:
            options.append("readonly")
        return ",".join(options)

    @staticmethod
    def _require_plain_directory(root: Path, name: str) -> Path:
        path = root / name
        if not path.is_dir() or DockerCandidateExecutor._is_reparse_point(path):
            raise CandidateExecutionError(f"job {name} directory is missing or unsafe")
        for current, directories, files in os.walk(path, followlinks=False):
            for child_name in [*directories, *files]:
                child = Path(current, child_name)
                if DockerCandidateExecutor._is_reparse_point(child):
                    raise CandidateExecutionError(f"job {name} tree contains a link")
        return path

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        metadata = path.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        return path.is_symlink() or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)

    @staticmethod
    def _verify_candidate_files(root: Path, manifest: CandidateJobManifest) -> None:
        actual_paths = sorted(
            path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
        )
        expected_paths = sorted(manifest.candidate_files)
        if actual_paths != expected_paths:
            raise CandidateExecutionError("candidate file set changed after static approval")
        if sorted(manifest.candidate_file_sha256) != expected_paths:
            raise CandidateExecutionError("candidate file hashes are missing")
        for relative_path in expected_paths:
            digest = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
            if digest != manifest.candidate_file_sha256[relative_path]:
                raise CandidateExecutionError("candidate file changed after static approval")

    def _remove_container(self, container_name: str) -> None:
        assert self.docker_cli is not None
        subprocess.run(
            [str(self.docker_cli), "rm", "--force", container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )

    @staticmethod
    def _write_control_file(path: Path, content: str) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


class DockerVerifiedToolExecutor:
    """Run one exact registered Python entrypoint in the hardened container."""

    def __init__(
        self,
        *,
        docker_cli: Path | None = None,
        runner_image: str = DEFAULT_RUNNER_IMAGE,
        limits: SandboxLimits | None = None,
    ) -> None:
        self.docker_cli = docker_cli or find_docker_cli()
        if not _PINNED_IMAGE.fullmatch(runner_image):
            raise ValueError("runner_image must use a sha256 content digest")
        self.runner_image = runner_image
        self.limits = limits or SandboxLimits(timeout_seconds=120)

    def execute(
        self,
        *,
        run_id: str,
        candidate_root: Path,
        input_root: Path,
        output_root: Path,
        entrypoint: str,
    ) -> VerifiedToolContainerReport:
        if UUID(run_id).version != 4 or str(UUID(run_id)) != run_id:
            raise CandidateExecutionError("verified run ID is invalid")
        if self.docker_cli is None:
            raise CandidateExecutionError("Docker CLI is not available")
        candidate = DockerCandidateExecutor._require_plain_directory(
            candidate_root.parent, candidate_root.name
        )
        inputs = DockerCandidateExecutor._require_plain_directory(
            input_root.parent, input_root.name
        )
        outputs = DockerCandidateExecutor._require_plain_directory(
            output_root.parent, output_root.name
        )
        entrypoint_path = (candidate / entrypoint).resolve(strict=True)
        if not entrypoint_path.is_relative_to(candidate) or not entrypoint_path.is_file():
            raise CandidateExecutionError("registered entrypoint is missing or unsafe")
        if any(outputs.iterdir()):
            raise CandidateExecutionError("verified output directory must be empty")

        container_name = f"ask-ai-run-{run_id}"
        command = self._docker_command(
            container_name=container_name,
            candidate_root=candidate,
            input_root=inputs,
            output_root=outputs,
            entrypoint=entrypoint,
        )
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = _BoundedCapture(self.limits.max_output_bytes)
        stderr = _BoundedCapture(self.limits.max_output_bytes)
        threads = [
            threading.Thread(target=stdout.drain, args=(process.stdout,), daemon=True),
            threading.Thread(target=stderr.drain, args=(process.stderr,), daemon=True),
        ]
        for thread in threads:
            thread.start()

        timed_out = False
        try:
            exit_code = process.wait(timeout=self.limits.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._remove_container(container_name)
            process.kill()
            exit_code = None
        finally:
            for thread in threads:
                thread.join(timeout=5)

        return VerifiedToolContainerReport(
            run_id=run_id,
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=stdout.text(),
            stderr=stderr.text(),
            output_truncated=stdout.truncated or stderr.truncated,
            backend=BACKEND_NAME,
            runner_image=self.runner_image,
        )

    def _docker_command(
        self,
        *,
        container_name: str,
        candidate_root: Path,
        input_root: Path,
        output_root: Path,
        entrypoint: str,
    ) -> list[str]:
        assert self.docker_cli is not None
        return [
            str(self.docker_cli),
            "run",
            "--rm",
            "--name",
            container_name,
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "65532:65532",
            "--pids-limit",
            str(self.limits.pids),
            "--ulimit",
            "nofile=256:256",
            "--ulimit",
            "core=0:0",
            "--memory",
            f"{self.limits.memory_mb}m",
            "--cpus",
            str(self.limits.cpus),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--mount",
            DockerCandidateExecutor._bind_mount(
                candidate_root, "/workspace/candidate", read_only=True
            ),
            "--mount",
            DockerCandidateExecutor._bind_mount(input_root, "/workspace/input", read_only=True),
            "--mount",
            DockerCandidateExecutor._bind_mount(output_root, "/workspace/output", read_only=False),
            "--workdir",
            "/workspace/candidate",
            self.runner_image,
            "python",
            "-B",
            "-c",
            _VERIFIED_TOOL_HARNESS,
            entrypoint,
        ]

    def _remove_container(self, container_name: str) -> None:
        assert self.docker_cli is not None
        subprocess.run(
            [str(self.docker_cli), "rm", "--force", container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
