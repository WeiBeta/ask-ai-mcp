"""Thin FastMCP composition roots driven by explicit profiles."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fastmcp import FastMCP

from ask_ai_mcp.mcp_profiles import (
    PROFILE_DEFINITIONS,
    Capability,
    ProfileName,
    profiles_for_capability,
)


def create_profile_servers(
    *, version: str, instructions: Mapping[ProfileName, str]
) -> dict[ProfileName, FastMCP]:
    """Create all compatibility servers from immutable profile definitions."""

    return {
        name: FastMCP(
            definition.display_name,
            instructions=instructions[name],
            version=version,
        )
        for name, definition in PROFILE_DEFINITIONS.items()
    }


def capability_tool(
    servers: Mapping[ProfileName, FastMCP], capability: Capability, **tool_kwargs: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register one tool only on profiles that explicitly own its capability."""

    def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
        for profile in profiles_for_capability(capability):
            servers[profile].tool(**tool_kwargs)(function)
        return function

    return decorator
