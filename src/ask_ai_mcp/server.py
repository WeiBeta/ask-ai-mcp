"""FastMCP server with a deliberately narrow initial tool surface."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from ask_ai_mcp import __version__
from ask_ai_mcp.accounting import AccountingServices
from ask_ai_mcp.code_review import CodeReviewManager
from ask_ai_mcp.code_review_models import (
    CodeReviewBackendStatus,
    CodeReviewStagedPatch,
    CodeReviewStagePatchCommand,
    CodeReviewStatus,
    CodeReviewStatusCommand,
    CodeReviewSubmission,
    CodeReviewSubmitCommand,
)
from ask_ai_mcp.coding import CodingManager
from ask_ai_mcp.coding_models import (
    CodingBackendStatus,
    CodingStatus,
    CodingStatusCommand,
    CodingSubmission,
    CodingSubmitCommand,
)
from ask_ai_mcp.collaboration import ReviewCollaborationService
from ask_ai_mcp.guidance import workflow_guidance_for
from ask_ai_mcp.h3 import H3ComfyClient
from ask_ai_mcp.lifecycle import CandidateLifecycle
from ask_ai_mcp.mcp_composition import capability_tool, create_profile_servers
from ask_ai_mcp.mcp_profiles import Capability, ProfileName
from ask_ai_mcp.models import (
    CandidateApprovalCommand,
    CandidateApprovalRequest,
    CandidateDecision,
    CandidateLifecycleResult,
    CandidateReviewBundle,
    CandidateReviewSummary,
    DeepSeekModel,
    H3BackendStatus,
    H3GenerationCommand,
    H3JobReport,
    H3JobSubmission,
    H3PostprocessCommand,
    H3PostprocessSubmission,
    PendingReviewList,
    RegisteredToolList,
    ReviewMode,
    SourceBackendStatus,
    SourceExtractionCommand,
    SourceJobReport,
    SourceJobSubmission,
    ToolBuildSpec,
    ToolCapability,
    UsageSummary,
    VerifiedToolExecutionCommand,
    VerifiedToolExecutionReport,
    VerifiedToolRecord,
    WorkflowGuidance,
    WorkflowGuidanceTopic,
)
from ask_ai_mcp.promotion import VerifiedToolRegistry
from ask_ai_mcp.protocol_audit import ProtocolAuditMiddleware
from ask_ai_mcp.provider import create_toolsmith_worker, load_worker_route
from ask_ai_mcp.qwen_source import load_source_backend
from ask_ai_mcp.review import CandidateReviewRepository
from ask_ai_mcp.review_attestation import ReviewAttestationStore
from ask_ai_mcp.sandbox import docker_backend_status
from ask_ai_mcp.source import SourceJobManager
from ask_ai_mcp.storage_retention import StorageRetentionManager
from ask_ai_mcp.usage import UsageStore
from ask_ai_mcp.verified_execution import VerifiedToolRunner
from ask_ai_mcp.workspace import CandidateWorkspaceManager

SERVER_INSTRUCTIONS = """
Ask AI exposes constrained external-model workers only. Never
use this server to draft or co-author final document prose, decide facts,
resolve source conflicts, or generate delivery-ready conclusions. Opus/Sol is
the controller and final authority. Externally generated code is untrusted: it
must remain outside the repository, pass isolated tests, and receive explicit
Opus/Sol approval before it can process real file copies. Original source files
are always read-only. Do not send secrets or entire knowledge bases.
""".strip()

H3_SERVER_INSTRUCTIONS = """
Use these tools only for bounded local MiniMax H3 video generation and explicit
ComfyUI post-processing. Poll submitted jobs with h3_job_status; terminal jobs
release model GPU memory while leaving ComfyUI running.
""".strip()

SOURCE_SERVER_INSTRUCTIONS = """
Use these tools only for bounded, source-faithful multimodal extraction from
allow-listed file copies. Results are canonical evidence with provenance, not
final prose or conclusions.
""".strip()

CODE_REVIEW_SERVER_INSTRUCTIONS = """
Use these tools only for bounded, read-only review of immutable snapshots from
explicitly allow-listed repositories. The reviewer cannot modify a repository,
run tests or shell commands, access arbitrary files, choose arbitrary models,
or produce patches. Findings are untrusted suggestions for Sol/human blind
adjudication. Keep this optional module disabled during ordinary development.
""".strip()

CODING_SERVER_INSTRUCTIONS = """
Use these tools only to request bounded, untrusted coding candidates from an
immutable allow-listed repository snapshot. The worker can replace only named
target files and cannot modify the repository, run commands, access arbitrary
paths, choose arbitrary models, or apply its patch. Sol must review, test, and
apply any accepted candidate.
""".strip()

_PROFILE_SERVERS = create_profile_servers(
    version=__version__,
    instructions={
        ProfileName.FULL: SERVER_INSTRUCTIONS,
        ProfileName.CORE: SERVER_INSTRUCTIONS,
        ProfileName.SUBAGENT: SERVER_INSTRUCTIONS,
        ProfileName.PERCEPTION: SOURCE_SERVER_INSTRUCTIONS,
        ProfileName.H3: H3_SERVER_INSTRUCTIONS,
        ProfileName.REVIEW: CODE_REVIEW_SERVER_INSTRUCTIONS,
        ProfileName.CODING: CODING_SERVER_INSTRUCTIONS,
    },
)

# Compatibility exports remain stable while entrypoints migrate to profile lookup.
mcp = _PROFILE_SERVERS[ProfileName.FULL]
core_mcp = _PROFILE_SERVERS[ProfileName.CORE]
subagent_mcp = _PROFILE_SERVERS[ProfileName.SUBAGENT]
source_mcp = _PROFILE_SERVERS[ProfileName.PERCEPTION]
h3_mcp = _PROFILE_SERVERS[ProfileName.H3]
review_mcp = _PROFILE_SERVERS[ProfileName.REVIEW]
coding_mcp = _PROFILE_SERVERS[ProfileName.CODING]


def profile_server(profile: ProfileName):
    """Return one explicit composition root without inferring capabilities."""

    return _PROFILE_SERVERS[profile]


def core_tool(**kwargs):
    """Register one shared tool on both the Core and Full servers."""

    return capability_tool(_PROFILE_SERVERS, Capability.TOOLSMITH, **kwargs)


def source_tool(**kwargs):
    """Register source-intelligence tools on Subagent and compatibility Full."""

    return capability_tool(_PROFILE_SERVERS, Capability.PERCEPTION, **kwargs)


def h3_tool(**kwargs):
    """Register local video tools on H3-only and compatibility Full."""

    return capability_tool(_PROFILE_SERVERS, Capability.H3, **kwargs)


def code_review_tool(**kwargs):
    """Register a tool only on the optional code-review server."""

    return capability_tool(_PROFILE_SERVERS, Capability.CODE_REVIEW, **kwargs)


def coding_tool(**kwargs):
    """Register a tool only on the optional coding-candidate server."""

    return capability_tool(_PROFILE_SERVERS, Capability.CODING, **kwargs)


_DESKTOP_CLIENT_NAMES = frozenset({"claude_desktop", "codex_desktop"})


@lru_cache(maxsize=1)
def get_usage_store() -> UsageStore:
    """Create the shared audit store lazily after MCP initialization."""
    return UsageStore()


@lru_cache(maxsize=1)
def get_storage_retention_manager() -> StorageRetentionManager:
    """Create the path-redacted local storage observer lazily."""

    return StorageRetentionManager()


@lru_cache(maxsize=1)
def get_accounting_services() -> AccountingServices:
    """Expose provider-neutral accounting services over the compatible store."""
    return AccountingServices.from_store(get_usage_store())


for _profile_mcp in _PROFILE_SERVERS.values():
    _profile_mcp.add_middleware(ProtocolAuditMiddleware(lambda: get_usage_store()))


@lru_cache(maxsize=1)
def get_toolsmith_provider():
    return load_worker_route()


@lru_cache(maxsize=1)
def get_workspace() -> CandidateWorkspaceManager:
    return CandidateWorkspaceManager()


@lru_cache(maxsize=1)
def get_review_repository() -> CandidateReviewRepository:
    return CandidateReviewRepository(get_workspace().jobs_root)


@lru_cache(maxsize=1)
def get_lifecycle() -> CandidateLifecycle:
    return CandidateLifecycle(
        client=create_toolsmith_worker(
            get_toolsmith_provider(), accounting=get_accounting_services()
        ),
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


@lru_cache(maxsize=1)
def get_review_collaboration() -> ReviewCollaborationService:
    return ReviewCollaborationService(
        repository=get_review_repository(),
        attestations=get_review_attestation_store(),
        registry=get_registry(),
        usage=get_usage_store(),
    )


@lru_cache(maxsize=1)
def get_h3_client() -> H3ComfyClient:
    return H3ComfyClient()


@lru_cache(maxsize=1)
def get_source_manager() -> SourceJobManager:
    return SourceJobManager(backend=load_source_backend())


@lru_cache(maxsize=1)
def get_code_review_manager() -> CodeReviewManager:
    return CodeReviewManager(accounting=get_accounting_services())


@lru_cache(maxsize=1)
def get_coding_manager() -> CodingManager:
    return CodingManager(accounting=get_accounting_services())


@coding_tool(
    annotations=ToolAnnotations(
        title="受限编码后端状态",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def coding_backend_status() -> CodingBackendStatus:
    """Check five fixed coding models, account ledger, and repository IDs."""

    return get_coding_manager().backend_status()


@coding_tool(
    annotations=ToolAnnotations(
        title="提交受限编码候选",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def coding_submit(command: CodingSubmitCommand) -> CodingSubmission:
    """Freeze selected files and request one repository-external candidate.

    Prefer DeepSeek V4 Flash or GLM-5.3-Flash. Use DeepSeek V4 Pro, GLM-5.3,
    or Kimi K3 only as an explicit advanced candidate for complex work. A
    failed request is never retried or routed to another model automatically.
    """

    return get_coding_manager().submit(command)


@coding_tool(
    annotations=ToolAnnotations(
        title="读取编码候选状态",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def coding_status(command: CodingStatusCommand) -> CodingStatus:
    """Read bounded metadata and a paginated candidate diff for Sol review."""

    return get_coding_manager().status(command)


@code_review_tool(
    annotations=ToolAnnotations(
        title="异构代码审查后端状态",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def code_review_backend_status() -> CodeReviewBackendStatus:
    """Check fixed OpenCode models, repository IDs, ledger, and blind monthly metrics."""

    return get_code_review_manager().backend_status()


@code_review_tool(
    annotations=ToolAnnotations(
        title="封存哈希固定的审查补丁",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def code_review_stage_patch(command: CodeReviewStagePatchCommand) -> CodeReviewStagedPatch:
    """Validate and atomically seal one bounded patch with a content-free receipt."""

    return get_code_review_manager().stage_patch(command)


@code_review_tool(
    annotations=ToolAnnotations(
        title="提交只读异构代码审查",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
def code_review_submit(command: CodeReviewSubmitCommand) -> CodeReviewSubmission:
    """Snapshot one diff and queue one policy-routed or explicit external Worker review."""

    return get_code_review_manager().submit(command)


@code_review_tool(
    annotations=ToolAnnotations(
        title="读取并盲审代码审查结果",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def code_review_status(command: CodeReviewStatusCommand) -> CodeReviewStatus:
    """Page findings and optionally record blind adjudication or delayed outcomes."""

    return get_code_review_manager().status(command)


def get_client_name() -> str:
    value = os.environ.get("ASK_AI_MCP_CLIENT_NAME", "")
    if value not in _DESKTOP_CLIENT_NAMES:
        raise RuntimeError(
            "ASK_AI_MCP_CLIENT_NAME must be claude_desktop or codex_desktop before "
            "candidate operations"
        )
    return value


@h3_tool(
    annotations=ToolAnnotations(
        title="MiniMax H3 本地后端状态",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def h3_backend_status() -> H3BackendStatus:
    """必要时启动本机 ComfyUI, 再检查 GPU 和必需的 MiniMax H3 模型。"""
    return get_h3_client().status()


@h3_tool(
    annotations=ToolAnnotations(
        title="提交 MiniMax H3 本地视频生成",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def h3_generate_video(command: H3GenerationCommand) -> H3JobSubmission:
    """异步提交本地 H3 文生视频或首尾帧生视频任务。

    仅连接本机回环地址, 输出含原生立体声音频。调用者须遵守 MiniMax H3
    许可地域和可接受使用政策; 任务提交后使用 h3_job_status 轮询。
    """
    return get_h3_client().submit(command)


@h3_tool(
    annotations=ToolAnnotations(
        title="后处理已筛选的 MiniMax H3 原片",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def h3_postprocess_video(command: H3PostprocessCommand) -> H3PostprocessSubmission:
    """对共享工作区中的已筛选 H3 原片执行显式插帧、超分或组合任务。

    调用者必须明确选择卡通或写实分组及具体模型; ComfyUI 不推断画风。
    原片不会被覆盖, 最终资产写入共享 outputs/postprocessed 目录。
    """
    return get_h3_client().submit_postprocess(command)


@h3_tool(
    annotations=ToolAnnotations(
        title="查询 MiniMax H3 本地视频任务",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def h3_job_status(
    prompt_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$")],
) -> H3JobReport:
    """查询异步 H3 任务并在完成后返回路径, 队列空闲时自动释放显存。"""
    return get_h3_client().job_status(prompt_id)


@source_tool(
    annotations=ToolAnnotations(
        title="Local multimodal source backend status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def source_backend_status() -> SourceBackendStatus:
    """Check the configured local source backend and allow-listed input boundary."""
    return get_source_manager().backend_status()


@source_tool(
    annotations=ToolAnnotations(
        title="Submit bounded multimodal source extraction",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def source_extract(command: SourceExtractionCommand) -> SourceJobSubmission:
    """Queue source-faithful evidence extraction from staged allow-listed file copies.

    This is not a general chat or writing tool. It accepts fixed extraction profiles,
    writes a dedicated canonical-evidence bundle, and never modifies original files.
    """
    return get_source_manager().submit(command)


@source_tool(
    annotations=ToolAnnotations(
        title="Read multimodal source extraction status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def source_job_status(
    job_id: Annotated[
        str,
        Field(
            min_length=36,
            max_length=36,
            pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
        ),
    ],
) -> SourceJobReport:
    """Return progress and hash-addressed evidence artifacts for one local source job."""
    return get_source_manager().job_status(job_id)


@core_tool(
    annotations=ToolAnnotations(
        title="Ask AI subscription usage status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def usage_status(days: Annotated[int, Field(ge=1, le=366)] = 15) -> UsageSummary:
    """Return usage, lifecycle economics, and path-free storage quotas; never calls a model."""
    summary = get_usage_store().summarize(days=days)
    return summary.model_copy(
        update={"storage_retention": get_storage_retention_manager().status()}
    )


@core_tool(
    annotations=ToolAnnotations(
        title="Read Ask AI workflow guidance",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def workflow_guidance(topic: WorkflowGuidanceTopic) -> WorkflowGuidance:
    """Load one local protocol topic on demand; never calls a model."""
    return workflow_guidance_for(topic)


@core_tool(
    annotations=ToolAnnotations(
        title="List cross-desktop pending reviews",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def list_pending_reviews() -> PendingReviewList:
    """Return a compact local queue with hashes, identities, blockers, and next action."""
    return get_review_collaboration().list_pending()


@core_tool(
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
) -> CandidateLifecycleResult:
    """Build and test one bounded helper-tool candidate for Sol/Opus review.

    This may call the configured worker and create disposable local jobs. It accepts only a
    structured tool specification, never an arbitrary prompt or source bundle.
    Provider and subscription gates are enforced by the backend. A passing
    result is still only review-pending and returns a compact summary rather
    than the full candidate patch.
    """
    client_name = get_client_name()
    backend = docker_backend_status()
    if not backend.ready:
        reasons = ",".join(backend.reasons) or "unknown"
        raise RuntimeError(f"isolated runner is unavailable: {reasons}")
    provider = get_toolsmith_provider()
    if provider.requires_budget_gate:
        raise RuntimeError(
            "direct DeepSeek CNY budget sessions are retired from the active Core protocol; "
            "select the configured subscription provider"
        )
    return get_lifecycle().run(
        spec,
        client_name=client_name,
        allow_pro=spec.model is DeepSeekModel.PRO,
    )


@core_tool(
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
        return get_review_collaboration().enrich_summary(repository.load_summary(job_id))
    review = repository.load(job_id)
    get_review_attestation_store().record(review, client_name=get_client_name())
    return review


@core_tool(
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


@core_tool(
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
    rehashed before it is returned. It does not call a model or inspect source
    files.
    """
    return get_verified_runner().list_registered_tools()


@core_tool(
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
