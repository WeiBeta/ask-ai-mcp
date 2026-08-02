"""FastMCP server with a deliberately narrow initial tool surface."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ask_ai_mcp import __version__
from ask_ai_mcp.budget import BudgetStore
from ask_ai_mcp.lifecycle import CandidateLifecycle
from ask_ai_mcp.models import (
    BudgetIncrementCommand,
    BudgetSessionCommand,
    BudgetSessionOpenCommand,
    BudgetSessionStatus,
    CandidateApprovalCommand,
    CandidateApprovalRequest,
    CandidateDecision,
    CandidateLifecycleResult,
    CandidateReviewBundle,
    CandidateReviewSummary,
    DeepSeekModel,
    RegisteredToolList,
    ReviewMode,
    ToolBuildSpec,
    ToolCapability,
    UsageSummary,
    VerifiedToolExecutionCommand,
    VerifiedToolExecutionReport,
    VerifiedToolRecord,
)
from ask_ai_mcp.promotion import VerifiedToolRegistry
from ask_ai_mcp.review import CandidateReviewRepository
from ask_ai_mcp.review_attestation import ReviewAttestationStore
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
def get_budget_store() -> BudgetStore:
    return BudgetStore(get_usage_store().path)


@lru_cache(maxsize=1)
def get_workspace() -> CandidateWorkspaceManager:
    return CandidateWorkspaceManager()


@lru_cache(maxsize=1)
def get_review_repository() -> CandidateReviewRepository:
    return CandidateReviewRepository(get_workspace().jobs_root)


@lru_cache(maxsize=1)
def get_lifecycle() -> CandidateLifecycle:
    return CandidateLifecycle(
        workspace=get_workspace(),
        review_repository=get_review_repository(),
        audit_store=get_usage_store(),
    )


@lru_cache(maxsize=1)
def get_review_attestation_store() -> ReviewAttestationStore:
    return ReviewAttestationStore(get_usage_store().path)


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
        title="Open a local DeepSeek budget session",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def open_budget_session(
    command: BudgetSessionOpenCommand | None = None,
) -> BudgetSessionStatus:
    """Create one conversation budget with Flash CNY 5 and Pro CNY 0.

    This is local and prompt-free. The returned opaque session ID must be
    reused by the same Claude/Codex conversation for every billed build.
    """
    client_name = get_client_name()
    command = command or BudgetSessionOpenCommand()
    return get_budget_store().open_session(client_name=client_name, label=command.label)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read a local DeepSeek budget session",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def budget_status(command: BudgetSessionCommand) -> BudgetSessionStatus:
    """Return prompt-free model grants and actual locally estimated spend."""
    return get_budget_store().status(
        command.budget_session_id,
        client_name=get_client_name(),
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Add one confirmed CNY 5 budget block",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def add_budget_block(command: BudgetIncrementCommand) -> BudgetSessionStatus:
    """Add exactly CNY 5 for one model after explicit user confirmation.

    Callers cannot choose the amount. For Pro, the first block changes the
    default zero grant into an active CNY 5 grant for this conversation.
    """
    return get_budget_store().add_budget_block(
        command.budget_session_id,
        client_name=get_client_name(),
        model=command.model,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Close a local DeepSeek budget session",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def close_budget_session(command: BudgetSessionCommand) -> BudgetSessionStatus:
    """Close a conversation budget without deleting its audit history."""
    return get_budget_store().close_session(
        command.budget_session_id,
        client_name=get_client_name(),
    )


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
    budget_session_id: Annotated[
        str,
        Field(
            min_length=36,
            max_length=36,
            pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
        ),
    ],
    spec: ToolBuildSpec,
) -> CandidateLifecycleResult:
    """Build and test one bounded helper-tool candidate for Sol/Opus review.

    This may call DeepSeek and create disposable local jobs. It accepts only a
    structured tool specification, never an arbitrary prompt or source bundle.
    Flash thinking is the default. Pro requires an active Pro budget block in
    the same conversation session. A passing result is still only review-pending
    and returns a compact summary rather than the full candidate patch.
    """
    client_name = get_client_name()
    backend = docker_backend_status()
    if not backend.ready:
        reasons = ",".join(backend.reasons) or "unknown"
        raise RuntimeError(f"isolated runner is unavailable: {reasons}")
    get_budget_store().require_lifecycle_budget(
        budget_session_id,
        client_name=client_name,
        model=spec.model,
    )
    return get_lifecycle().run(
        spec,
        client_name=client_name,
        budget_session_id=budget_session_id,
        allow_pro=spec.model is DeepSeekModel.PRO,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Review isolated tool candidate",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
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
    mode: ReviewMode = ReviewMode.SUMMARY,
) -> CandidateReviewSummary | CandidateReviewBundle:
    """Return a compact review by default, or attest an exact full patch.

    This performs no model or network call and does not approve or execute the
    candidate. Full mode revalidates every byte and records the configured
    desktop identity, candidate hash, and patch hash for the later approval gate.
    """
    repository = get_review_repository()
    if mode is ReviewMode.SUMMARY:
        return repository.load_summary(job_id)
    review = repository.load(job_id)
    get_review_attestation_store().record(review, client_name=get_client_name())
    return review


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
    requires_full_review = (
        ToolCapability.WRITE_DEDICATED_OUTPUT in command.allowed_capabilities
        or review.attempts[-1].model is DeepSeekModel.PRO
    )
    if requires_full_review:
        get_review_attestation_store().require(review, client_name=client_name)
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
