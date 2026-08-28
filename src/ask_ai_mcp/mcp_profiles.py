"""Provider-neutral MCP capability and executable profile contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Capability(StrEnum):
    TOOLSMITH = "toolsmith"
    PERCEPTION = "perception"
    H3 = "h3"
    CODE_REVIEW = "code_review"
    CODING = "coding"


class ProfileName(StrEnum):
    FULL = "full"
    CORE = "core"
    SUBAGENT = "subagent"
    PERCEPTION = "perception"
    H3 = "h3"
    REVIEW = "review"
    CODING = "coding"


@dataclass(frozen=True, slots=True)
class ProfileDefinition:
    display_name: str
    capabilities: tuple[Capability, ...]


PROFILE_DEFINITIONS: dict[ProfileName, ProfileDefinition] = {
    ProfileName.FULL: ProfileDefinition(
        "Ask AI MCP", (Capability.H3, Capability.PERCEPTION, Capability.TOOLSMITH)
    ),
    ProfileName.CORE: ProfileDefinition("Ask AI MCP Core", (Capability.TOOLSMITH,)),
    ProfileName.SUBAGENT: ProfileDefinition(
        "Ask AI MCP Subagent", (Capability.PERCEPTION, Capability.TOOLSMITH)
    ),
    ProfileName.PERCEPTION: ProfileDefinition("Ask AI MCP Perception", (Capability.PERCEPTION,)),
    ProfileName.H3: ProfileDefinition("Ask AI MCP H3", (Capability.H3,)),
    ProfileName.REVIEW: ProfileDefinition("Ask AI MCP Code Review", (Capability.CODE_REVIEW,)),
    ProfileName.CODING: ProfileDefinition("Ask AI MCP Coding", (Capability.CODING,)),
}

PROFILE_ALIASES: dict[str, ProfileName] = {
    "full": ProfileName.FULL,
    "core": ProfileName.CORE,
    "subagent": ProfileName.SUBAGENT,
    "perception": ProfileName.PERCEPTION,
    "source": ProfileName.PERCEPTION,
    "h3": ProfileName.H3,
    "review": ProfileName.REVIEW,
    "code-review": ProfileName.REVIEW,
    "coding": ProfileName.CODING,
    "code": ProfileName.CODING,
}


def resolve_profile(value: str) -> ProfileName:
    """Resolve one compatibility alias without enabling an adjacent capability."""

    normalized = value.strip().casefold()
    try:
        return PROFILE_ALIASES[normalized]
    except KeyError as error:
        allowed = ", ".join(PROFILE_DEFINITIONS)
        raise ValueError(f"MCP profile must be one of: {allowed}") from error


def profiles_for_capability(capability: Capability) -> tuple[ProfileName, ...]:
    """Return deterministic composition targets for one bounded capability."""

    return tuple(
        name
        for name, definition in PROFILE_DEFINITIONS.items()
        if capability in definition.capabilities
    )
