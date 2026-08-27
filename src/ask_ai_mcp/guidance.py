"""On-demand operating guidance that does not burden every tool schema."""

from ask_ai_mcp.models import WorkflowGuidance, WorkflowGuidanceTopic

_GUIDANCE: dict[WorkflowGuidanceTopic, list[str]] = {
    WorkflowGuidanceTopic.OVERVIEW: [
        "Ask AI workers are limited to helper-tool construction, source structuring, "
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
    WorkflowGuidanceTopic.USAGE: [
        "Call usage_status for prompt-free subscription-ledger status before external work.",
        "The USD 2 daily figure is a utilization pace derived from the shared monthly "
        "allowance, not a hard daily spending limit.",
        "Within an already configured subscription, calls need no per-call confirmation "
        "while backend ledger gates remain open.",
        "Shared rolling windows, model allowances, and an authoritative provider 429 can "
        "block work; never retry a paid failure automatically.",
        "Adding an account, subscription, top-up, or allowance still requires explicit "
        "user authorization.",
    ],
    WorkflowGuidanceTopic.BUILD: [
        "Call build_helper_tool only with a bounded structured ToolBuildSpec after "
        "checking the current provider and subscription ledger.",
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
