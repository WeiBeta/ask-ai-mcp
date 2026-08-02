"""FastMCP server with a deliberately narrow initial tool surface."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ask_ai_mcp import __version__
from ask_ai_mcp.lifecycle import CandidateLifecycle
from ask_ai_mcp.models import (
    CandidateApprovalCommand,
    CandidateApprovalRequest,
    CandidateDecision,
    CandidateLifecycleResult,
    CandidateReviewBundle,
    RegisteredToolList,
    ToolBuildSpec,
    UsageSummary,
    VerifiedToolExecutionCommand,
    VerifiedToolExecutionReport,
    VerifiedToolRecord,
)
from ask_ai_mcp.promotion import VerifiedToolRegistry
from ask_ai_mcp.review import CandidateReviewRepository
from ask_ai_mcp.sandbox import docker_backend_status
from ask_ai_mcp.usage import UsageStore
from ask_ai_mcp.verified_execution import VerifiedToolRunner
from ask_ai_mcp.workspace import CandidateWorkspaceManager

SERVER_INSTRUCTIONS = """
DeepSeek is a constrained toolsmith and source-structuring worker only. Never
use this server to draft or co-author final document prose, decide facts,
resolve source conflicts, or generate delivery-ready conclusions. Opus/Sol is
the controller and final authority. DeepSeek-generated code is untrusted: it
must remain outside the repository, pass isolated tests, and receive explicit
Opus/Sol approval before it can process real file copies. Original source files
are always read-only. Do not send secrets or entire knowledge bases.
""".strip()

mcp = FastMCP(
    "Ask AI MCP",
    instructions=SERVER_INSTRUCTIONS,
    version=__version__,
)

_DESKTOP_CLIENT_NAMES = frozenset({"claude_desktop", "codex_desktop"})


@lru_cache(maxsize=1)
def get_usage_store() -> UsageStore:
    """Create the shared audit store lazily after MCP initialization."""
    return UsageStore()


@lru_cache(maxsize=1)
def get_workspace() -> CandidateWorkspaceManager:
    return CandidateWorkspaceManager()


@lru_cache(maxsize=1)
def get_review_repository() -> CandidateReviewRepository:
    return CandidateReviewRepository(get_workspace().jobs_root)


@lru_cache(maxsize=1)
def get_lifecycle() -> CandidateLifecycle:
    return CandidateLifecycle(workspace=get_workspace(), review_repository=get_review_repository())


@lru_cache(maxsize=1)
def get_registry() -> VerifiedToolRegistry:
    return VerifiedToolRegistry(jobs_root=get_workspace().jobs_root)


@lru_cache(maxsize=1)
def get_verified_runner() -> VerifiedToolRunner:
    return VerifiedToolRunner(registry=get_registry())


def get_client_name() -> str:
    value = os.environ.get("ASK_AI_MCP_CLIENT_NAME", "")
    if value not in _DESKTOP_CLIENT_NAMES:
        raise RuntimeError(
            "ASK_AI_MCP_CLIENT_NAME must be claude_desktop or codex_desktop before "
            "candidate operations"
        )
    return value


@mcp.tool(
    annotations=ToolAnnotations(
        title="DeepSeek usage status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def usage_status(days: Annotated[int, Field(ge=1, le=366)] = 15) -> UsageSummary:
    """Return prompt-free DeepSeek usage totals for the requested period.

    This tool performs no external API call and never returns prompts, source
    contents, responses, or credentials. Candidate build, review, and approval
    are separate tools; this status call cannot trigger any of them.
    """
    return get_usage_store().summarize(days=days)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Build isolated helper-tool candidate",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def build_helper_tool(
    spec: ToolBuildSpec,
    allow_pro: bool = False,
) -> CandidateLifecycleResult:
    """Build and test one bounded helper-tool candidate for Sol/Opus review.

    This may call DeepSeek and create disposable local jobs. It accepts only a
    structured tool specification, never an arbitrary prompt or source bundle.
    Flash thinking is the default. Pro requires both `spec.model=deepseek-v4-pro`
    and explicit `allow_pro=true`. A passing result is still only review-pending.
    """
    client_name = get_client_name()
    backend = docker_backend_status()
    if not backend.ready:
        reasons = ",".join(backend.reasons) or "unknown"
        raise RuntimeError(f"isolated runner is unavailable: {reasons}")
    return get_lifecycle().run(spec, client_name=client_name, allow_pro=allow_pro)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Review isolated tool candidate",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def review_tool_candidate(
    job_id: Annotated[
        str,
        Field(
            min_length=36,
            max_length=36,
            pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
        ),
    ],
) -> CandidateReviewBundle:
    """Revalidate and return the exact patch, tests, findings, and risks.

    This performs no model or network call and does not approve or execute the
    candidate. Any changed review metadata or candidate byte causes rejection.
    """
    return get_review_repository().load(job_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Approve exact tested tool candidate",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def approve_tool_candidate(command: CandidateApprovalCommand) -> VerifiedToolRecord:
    """Promote an exact reviewed hash without running it on real files.

    The desktop host identity is taken from server configuration, not caller
    input. Modified candidates cannot be approved; they need a fresh build and
    isolated test run. This tool only copies verified bytes into the registry.
    """
    client_name = get_client_name()
    review = get_review_repository().load(command.job_id)
    if review.candidate_sha256 != command.candidate_sha256:
        raise RuntimeError("approval hash does not match the reviewed candidate")
    request = CandidateApprovalRequest(
        job_id=command.job_id,
        candidate_sha256=command.candidate_sha256,
        version=command.version,
        approved_by=client_name,
        decision=CandidateDecision.APPROVED,
        allowed_capabilities=command.allowed_capabilities,
    )
    _, record = get_registry().approve(
        job_root=get_workspace().jobs_root / command.job_id,
        request=request,
    )
    return record


@mcp.tool(
    annotations=ToolAnnotations(
        title="List hash-pinned registered tools",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def list_registered_tools() -> RegisteredToolList:
    """List locally registered tools, execution counts, and blocking reasons.

    This is a local, prompt-free registry read. Every candidate tree is
    rehashed before it is returned. It does not call DeepSeek or inspect source
    files.
    """
    return get_verified_runner().list_registered_tools()


@mcp.tool(
    annotations=ToolAnnotations(
        title="Run an exact approved tool on staged copies",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def run_verified_tool(
    command: VerifiedToolExecutionCommand,
) -> VerifiedToolExecutionReport:
    """Run one exact registered hash in Docker without modifying source files.

    Source paths must be plain files under explicitly configured allowed roots.
    The server copies them into a private run directory, mounts those copies
    read-only, writes only to a dedicated output directory, and returns hashes
    rather than file contents. Tools lacking the standard execution contract,
    output capability, or required dual-desktop approval are rejected.
    """
    client_name = get_client_name()
    backend = docker_backend_status()
    if not backend.ready:
        reasons = ",".join(backend.reasons) or "unknown"
        raise RuntimeError(f"isolated runner is unavailable: {reasons}")
    return get_verified_runner().run(command, client_name=client_name)
