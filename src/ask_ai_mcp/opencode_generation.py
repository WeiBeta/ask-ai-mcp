"""Central OpenCode Go generation policies shared by bounded MCP modules."""

from __future__ import annotations

from dataclasses import dataclass

from ask_ai_mcp.opencode_pricing import OpenCodeGoModel

WIDE_MAX_OUTPUT_TOKENS = 131_072


@dataclass(frozen=True, slots=True)
class OpenCodeGenerationPolicy:
    reasoning_effort: str
    max_output_tokens: int
    context_tokens: int = 1_000_000


OPENCODE_GENERATION_POLICIES = {
    OpenCodeGoModel.GLM_5_3_FLASH: OpenCodeGenerationPolicy(
        reasoning_effort="max", max_output_tokens=WIDE_MAX_OUTPUT_TOKENS
    ),
    OpenCodeGoModel.GLM_5_3: OpenCodeGenerationPolicy(
        reasoning_effort="max", max_output_tokens=WIDE_MAX_OUTPUT_TOKENS
    ),
    OpenCodeGoModel.KIMI_K3: OpenCodeGenerationPolicy(
        reasoning_effort="max", max_output_tokens=WIDE_MAX_OUTPUT_TOKENS
    ),
    OpenCodeGoModel.DSV4_FLASH: OpenCodeGenerationPolicy(
        reasoning_effort="max", max_output_tokens=WIDE_MAX_OUTPUT_TOKENS
    ),
    OpenCodeGoModel.DSV4_PRO: OpenCodeGenerationPolicy(
        reasoning_effort="max", max_output_tokens=WIDE_MAX_OUTPUT_TOKENS
    ),
    OpenCodeGoModel.GPT_5_6_LUNA: OpenCodeGenerationPolicy(
        reasoning_effort="high", max_output_tokens=WIDE_MAX_OUTPUT_TOKENS
    ),
    OpenCodeGoModel.QWEN_3_8_MAX: OpenCodeGenerationPolicy(
        reasoning_effort="none", max_output_tokens=32_768
    ),
    OpenCodeGoModel.GROK_4_6: OpenCodeGenerationPolicy(
        reasoning_effort="max", max_output_tokens=WIDE_MAX_OUTPUT_TOKENS, context_tokens=500_000
    ),
}


def opencode_generation_policy(model: OpenCodeGoModel) -> OpenCodeGenerationPolicy:
    """Return the pinned client-side policy for one fixed Go route."""

    return OPENCODE_GENERATION_POLICIES[model]
