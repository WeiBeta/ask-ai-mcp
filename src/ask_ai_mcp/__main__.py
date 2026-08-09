"""Process entry points for the Core and Full STDIO MCP profiles."""

from __future__ import annotations

import os

from ask_ai_mcp.server import core_mcp, mcp

_PROFILE_ENV = "ASK_AI_MCP_PROFILE"


def selected_mcp(profile: str | None = None):
    """Select a validated server profile without silently enabling H3."""

    value = (profile if profile is not None else os.environ.get(_PROFILE_ENV, "full")).strip()
    normalized = value.casefold()
    if normalized == "core":
        return core_mcp
    if normalized == "full":
        return mcp
    raise RuntimeError(f"{_PROFILE_ENV} must be core or full")


def main() -> None:
    """Run the selected profile without writing non-protocol data to stdout."""
    selected_mcp().run()


def main_core() -> None:
    """Run the Core profile with no H3 tools registered."""
    core_mcp.run()


def main_full() -> None:
    """Run the Full profile including the H3 adapter tools."""
    mcp.run()


if __name__ == "__main__":
    main()
