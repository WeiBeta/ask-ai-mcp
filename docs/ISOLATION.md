# Candidate isolation

## Selected backend

Phase 2 uses Docker Desktop in all-users mode with its WSL 2 Linux-container
backend. Windows Sandbox remains a possible future fallback, but it is not the
primary implementation.

The host meets the hardware baseline: Windows 11 Pro, hardware virtualization,
and ample memory. WSL 2 and Docker Desktop are installed as machine-level
components. Docker Desktop resides under
`C:\Program Files\Docker\Docker`; its WSL data disks use Docker's standard
per-user data location.

Post-reboot verification on 2026-08-02 confirmed WSL 2.7.11, Docker Engine
29.6.2 on Linux/amd64, and working CPU, memory, and PID controls. Windows
Containers, Hyper-V, WSL, Virtual Machine Platform, .NET Framework 3.5, and
.NET 4 advanced services all remained enabled.

This is a multipurpose physical machine. Host configuration follows an
additive-only rule: the project does not disable Windows Containers, Hyper-V,
legacy .NET Framework versions, IIS, or other shared capabilities. The runner's
Linux-only requirement is enforced per `docker run`, not by removing host
features.

## Why static analysis is insufficient

AST analysis catches common forbidden imports, dynamic execution, reflection,
and absolute paths. It cannot prove arbitrary Python safe. A candidate that
passes static checks is only eligible for container staging; it is never safe
to run as an ordinary Windows child process.

## Planned container boundary

Every candidate run will use a fresh Linux container with:

- `--network none`;
- a read-only root filesystem;
- all Linux capabilities dropped;
- `no-new-privileges`;
- a non-root UID and GID;
- process, CPU, memory, output, and wall-clock limits;
- candidate code and fixture inputs mounted read-only;
- one dedicated output directory mounted read-write;
- no Docker socket, credentials, Git directory, user profile, or repository;
- automatic container removal after completion.

The initial runtime is content-pinned as
`python@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64`
(resolved from `python:3.13.14-slim-bookworm` on 2026-08-02).

Official isolation controls:

- <https://docs.docker.com/engine/containers/run/>
- <https://docs.docker.com/engine/network/drivers/none/>
- <https://docs.docker.com/reference/cli/docker/container/run>

## Workspace layout

Candidates are staged under `%LOCALAPPDATA%\AskAIMCP\jobs\<job-id>`:

```text
control/    manifest and static-analysis report
candidate/  generated files, mounted read-only
input/      copied or synthetic fixtures, mounted read-only
output/     the only writable host mapping
```

No job directory is created until the candidate hash matches and static policy
allows it. Staging never executes candidate code.

## Backend readiness

Run:

```powershell
uv run ask-ai-mcp-runner status
```

The backend is ready only when the Docker CLI, Docker engine, and pinned runner
image are all available. The internal executor runs Python compilation and
stdlib `unittest` discovery using a fixed host-controlled harness. Candidate
test execution is reachable only through `build_helper_tool`; callers cannot
choose container commands, images, mounts, limits, or inputs. Execution of
approved tools against real file copies remains unimplemented and unexposed.
