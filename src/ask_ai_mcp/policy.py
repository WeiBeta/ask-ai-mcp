"""Hard delegation policy for DeepSeek toolsmith requests."""

from __future__ import annotations

import re

from ask_ai_mcp.models import PolicyDecision, RuntimeKind, ToolBuildSpec

SAFE_PYTHON_PACKAGES = frozenset(
    {
        "openpyxl",
        "pillow",
        "pymupdf",
        "pypdf",
        "python-docx",
        "python-pptx",
    }
)

_FINAL_PROSE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"(?:撰写|代写|续写|共撰|生成).{0,16}(?:正文|章节|报告|方案|总结|结论|稿件)",
        r"(?:正文|章节|报告|方案|总结|结论).{0,16}(?:撰写|代写|续写|共撰|生成)",
        r"(?:共同撰写|联合撰写|最终成稿|交付正文)",
        r"(?:write|draft|compose|continue|co[- ]?author).{0,24}"
        r"(?:report|chapter|proposal|final prose|executive summary|conclusion)",
        r"(?:report|chapter|proposal|final prose|executive summary|conclusion).{0,24}"
        r"(?:write|draft|compose|continue|co[- ]?author)",
    )
)


class PolicyViolation(ValueError):
    """Raised when a request crosses a non-overridable delegation boundary."""


def evaluate_tool_spec(spec: ToolBuildSpec) -> PolicyDecision:
    """Return a deterministic policy decision for a proposed tool build."""
    reasons: list[str] = []
    searchable = "\n".join(
        [
            spec.purpose,
            spec.input_contract,
            spec.output_contract,
            *spec.acceptance_tests,
            spec.fixture_notes or "",
        ]
    )

    if any(pattern.search(searchable) for pattern in _FINAL_PROSE_PATTERNS):
        reasons.append("final_document_prose_is_reserved_for_opus_or_sol")

    if spec.runtime is RuntimeKind.POWERSHELL:
        reasons.append("powershell_candidates_are_not_enabled_in_phase_0")

    normalized_packages = {package.casefold() for package in spec.allowed_packages}
    unsupported = sorted(normalized_packages - SAFE_PYTHON_PACKAGES)
    if unsupported:
        reasons.append(f"packages_not_allow_listed:{','.join(unsupported)}")

    return PolicyDecision(allowed=not reasons, reasons=reasons)


def require_tool_spec_allowed(spec: ToolBuildSpec) -> None:
    """Raise a stable error when a tool specification is not delegable."""
    decision = evaluate_tool_spec(spec)
    if not decision.allowed:
        raise PolicyViolation(";".join(decision.reasons))
