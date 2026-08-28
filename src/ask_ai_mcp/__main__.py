"""Process entry points for the Core and Full STDIO MCP profiles."""

from __future__ import annotations

import os

from ask_ai_mcp.mcp_profiles import ProfileName, resolve_profile
from ask_ai_mcp.server import profile_server

_PROFILE_ENV = "ASK_AI_MCP_PROFILE"


def selected_mcp(profile: str | None = None):
    """Select a validated server profile without silently enabling H3."""

    value = (profile if profile is not None else os.environ.get(_PROFILE_ENV, "full")).strip()
    try:
        return profile_server(resolve_profile(value))
    except ValueError as error:
        raise RuntimeError(
            f"{_PROFILE_ENV} must be core, subagent, perception, h3, coding, review, or full"
        ) from error


def main() -> None:
    """Run the selected profile without writing non-protocol data to stdout."""
    selected_mcp().run()


def main_core() -> None:
    """Run the Core profile with no H3 tools registered."""
    profile_server(ProfileName.CORE).run()


def main_full() -> None:
    """Run the Full profile including the H3 adapter tools."""
    profile_server(ProfileName.FULL).run()


def main_subagent() -> None:
    """Run toolsmith plus source intelligence without H3 tools."""
    profile_server(ProfileName.SUBAGENT).run()


def main_h3() -> None:
    """Run only the four local H3 and ComfyUI tools."""
    profile_server(ProfileName.H3).run()


def main_perception() -> None:
    """Run only the three bounded multimodal source tools."""
    profile_server(ProfileName.PERCEPTION).run()


def main_review() -> None:
    """Run only the three bounded heterogeneous code-review tools."""
    profile_server(ProfileName.REVIEW).run()


def main_coding() -> None:
    """Run only the three bounded coding-candidate tools."""

    profile_server(ProfileName.CODING).run()


if __name__ == "__main__":
    main()
