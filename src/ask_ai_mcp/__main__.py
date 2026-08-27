"""Process entry points for the Core and Full STDIO MCP profiles."""

from __future__ import annotations

import os

from ask_ai_mcp.server import (
    coding_mcp,
    core_mcp,
    h3_mcp,
    mcp,
    review_mcp,
    source_mcp,
    subagent_mcp,
)

_PROFILE_ENV = "ASK_AI_MCP_PROFILE"


def selected_mcp(profile: str | None = None):
    """Select a validated server profile without silently enabling H3."""

    value = (profile if profile is not None else os.environ.get(_PROFILE_ENV, "full")).strip()
    normalized = value.casefold()
    if normalized == "core":
        return core_mcp
    if normalized == "full":
        return mcp
    if normalized == "subagent":
        return subagent_mcp
    if normalized in {"perception", "source"}:
        return source_mcp
    if normalized == "h3":
        return h3_mcp
    if normalized in {"review", "code-review"}:
        return review_mcp
    if normalized in {"coding", "code"}:
        return coding_mcp
    raise RuntimeError(
        f"{_PROFILE_ENV} must be core, subagent, perception, h3, coding, review, or full"
    )


def main() -> None:
    """Run the selected profile without writing non-protocol data to stdout."""
    selected_mcp().run()


def main_core() -> None:
    """Run the Core profile with no H3 tools registered."""
    core_mcp.run()


def main_full() -> None:
    """Run the Full profile including the H3 adapter tools."""
    mcp.run()


def main_subagent() -> None:
    """Run toolsmith plus source intelligence without H3 tools."""
    subagent_mcp.run()


def main_h3() -> None:
    """Run only the four local H3 and ComfyUI tools."""
    h3_mcp.run()


def main_perception() -> None:
    """Run only the three bounded multimodal source tools."""
    source_mcp.run()


def main_review() -> None:
    """Run only the three bounded heterogeneous code-review tools."""
    review_mcp.run()


def main_coding() -> None:
    """Run only the three bounded coding-candidate tools."""

    coding_mcp.run()


if __name__ == "__main__":
    main()
