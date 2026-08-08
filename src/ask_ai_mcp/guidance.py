"""On-demand operating guidance that does not burden every tool schema."""

from ask_ai_mcp.models import WorkflowGuidance, WorkflowGuidanceTopic

_GUIDANCE: dict[WorkflowGuidanceTopic, list[str]] = {
    WorkflowGuidanceTopic.OVERVIEW: [
        "DeepSeek is limited to helper-tool construction, source structuring, "
        "and mechanical code work.",
        "Never delegate final prose, facts, conclusions, source-conflict decisions, "
        "or delivery wording.",
        "Delegate only mechanically verifiable work likely to need at least three "
        "implementation rounds or to produce a reusable registered tool.",
        "If the specification is not clearly shorter than the expected artifact, "
        "do the task in the controller model.",
        "Send bounded specifications and synthetic or minimized fixtures; never send "
        "secrets, full knowledge bases, or whole business documents.",
    ],
    WorkflowGuidanceTopic.BUDGET: [
        "Each Claude or Codex chat opens its own budget session once and reuses that "
        "opaque ID only inside the same chat.",
        "Flash starts with CNY 5; while active it needs no per-call confirmation.",
        "Flash extensions are exactly CNY 5 and require explicit user confirmation "
        "after the server reports extension_required.",
        "Pro starts at CNY 0; every first or renewed CNY 5 Pro block requires explicit "
        "user confirmation naming model, reason, and amount.",
        "A started lifecycle may finish and overshoot its current block; the next "
        "lifecycle is then blocked.",
    ],
    WorkflowGuidanceTopic.BUILD: [
        "Call build_helper_tool only with a bounded structured ToolBuildSpec and an "
        "active same-chat budget_session_id.",
        "Original business files are not build inputs; use synthetic or minimized "
        "redacted fixture descriptions.",
        "Flash thinking-high is the default. Static-policy repair is non-thinking and "
        "bounded; semantic repair is thinking-high and bounded.",
        "A passing build remains untrusted and review-pending; it is not repository "
        "code and cannot process real files.",
    ],
    WorkflowGuidanceTopic.REVIEW: [
        "Use list_pending_reviews to discover work created by either desktop without "
        "loading patches.",
        "Use summary mode for compact identity and test evidence; use full mode to "
        "load and attest the exact patch.",
        "Verify static findings, isolated test evidence, file hashes, scope, packages, "
        "and least-privilege capabilities.",
        "A changed candidate or patch invalidates prior review evidence and requires "
        "a fresh tested candidate.",
    ],
    WorkflowGuidanceTopic.APPROVAL: [
        "Approval promotes only the exact tested candidate hash; it does not run the tool.",
        "Pro candidates and tools that can write dedicated output require exact full "
        "review by the approving desktop.",
        "Pro or output-writing tools require both claude_desktop and codex_desktop "
        "approvals before execution.",
        "Approve only the minimum read and output capabilities needed by the execution contract.",
    ],
    WorkflowGuidanceTopic.RUN: [
        "Run only a registered, hash-pinned tool whose blocking_reasons list is empty.",
        "Input paths must be explicit plain files under configured allowed roots; the "
        "server stages read-only copies.",
        "The container writes only to a dedicated run output directory and returns "
        "artifact metadata and hashes.",
        "Treat outputs as preprocessing artifacts for controller review, never as "
        "automatically accepted final facts or prose.",
    ],
}


def workflow_guidance_for(topic: WorkflowGuidanceTopic) -> WorkflowGuidance:
    return WorkflowGuidance(topic=topic, guidance=_GUIDANCE[topic])
