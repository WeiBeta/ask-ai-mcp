"""Opt-in tests against the real Docker Desktop WSL 2 isolation boundary."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest

from ask_ai_mcp.models import CandidateJobManifest, CandidateJobState
from ask_ai_mcp.sandbox import DockerCandidateExecutor, docker_backend_status

pytestmark = pytest.mark.skipif(
    os.environ.get("ASK_AI_MCP_DOCKER_TESTS") != "1",
    reason="set ASK_AI_MCP_DOCKER_TESTS=1 to run real Docker isolation tests",
)


def test_real_container_enforces_candidate_boundaries(tmp_path: Path) -> None:
    status = docker_backend_status()
    assert status.ready, status.reasons

    job_id = str(uuid4())
    root = tmp_path / job_id
    for name in ("candidate", "input", "output", "control"):
        (root / name).mkdir(parents=True)
    (root / "input" / "fixture.txt").write_text("synthetic", encoding="utf-8")

    candidate = root / "candidate" / "test_boundaries.py"
    candidate.write_text(
        """import os
import pathlib
import socket
import unittest


class TestBoundaries(unittest.TestCase):
    def test_runtime_boundary(self):
        self.assertEqual(os.getuid(), 65532)
        status = pathlib.Path('/proc/self/status').read_text(encoding='utf-8')
        self.assertIn('NoNewPrivs:\\t1', status)
        self.assertIn('CapEff:\\t0000000000000000', status)
        self.assertFalse(pathlib.Path('/var/run/docker.sock').exists())
        self.assertNotIn('DEEPSEEK_API_KEY', os.environ)
        self.assertNotIn('GITHUB_TOKEN', os.environ)
        for target in (
            pathlib.Path('/workspace/candidate/escape.txt'),
            pathlib.Path('/workspace/input/escape.txt'),
            pathlib.Path('/escape.txt'),
        ):
            with self.assertRaises(OSError):
                target.write_text('blocked', encoding='utf-8')
        sock = socket.socket()
        sock.settimeout(0.5)
        self.assertNotEqual(sock.connect_ex(('1.1.1.1', 53)), 0)
        sock.close()
        pathlib.Path('/workspace/output/result.txt').write_text('ok', encoding='utf-8')
""",
        encoding="utf-8",
    )
    manifest = CandidateJobManifest(
        job_id=job_id,
        state=CandidateJobState.STATIC_APPROVED,
        tool_name="boundary_probe",
        spec_sha256="a" * 64,
        candidate_sha256="b" * 64,
        candidate_files=["test_boundaries.py"],
        candidate_file_sha256={
            "test_boundaries.py": hashlib.sha256(candidate.read_bytes()).hexdigest()
        },
    )
    (root / "control" / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )

    report = DockerCandidateExecutor().execute(root)

    assert report.state is CandidateJobState.EXECUTED
    assert report.exit_code == 0
    assert report.tests_run == 1
    assert report.timed_out is False
    assert report.output_truncated is False
    assert (root / "output" / "result.txt").read_text(encoding="utf-8") == "ok"
    assert not (root / "candidate" / "escape.txt").exists()
    assert not (root / "input" / "escape.txt").exists()
