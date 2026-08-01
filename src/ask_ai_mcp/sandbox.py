"""Readiness checks for the Docker Desktop WSL 2 execution backend."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ask_ai_mcp.models import SandboxBackendStatus

DEFAULT_RUNNER_IMAGE = "python:3.13.14-slim-bookworm"
SYSTEM_DOCKER_CLI = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")


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
                [str(docker_cli), "version", "--format", "{{.Server.Version}}"],
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
        else:
            image = subprocess.run(
                [str(docker_cli), "image", "inspect", DEFAULT_RUNNER_IMAGE],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            image_available = image.returncode == 0
            if not image_available:
                reasons.append("runner_image_not_available")

    return SandboxBackendStatus(
        backend="docker_desktop_wsl2",
        ready=not reasons,
        container_cli_available=docker_cli is not None,
        engine_available=engine_available,
        image_available=image_available,
        reasons=reasons,
    )
