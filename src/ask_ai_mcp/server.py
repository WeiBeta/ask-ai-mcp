"""FastMCP server with a deliberately narrow initial tool surface."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ask_ai_mcp.models import UsageSummary
from ask_ai_mcp.usage import UsageStore

SERVER_INSTRUCTIONS = """
DeepSeek is a constrained toolsmith and source-structuring worker only. Never
use this server to draft or co-author final document prose, decide facts,
resolve source conflicts, or generate delivery-ready conclusions. Opus/Sol is
the controller and final authority. DeepSeek-generated code is untrusted: it
must remain outside the repository, pass isolated tests, and receive explicit
Opus/Sol approval before it can process real file copies. Original source files
are always read-only. Do not send secrets or entire knowledge bases.
""".strip()

mcp = FastMCP(
    "Ask AI MCP",
    instructions=SERVER_INSTRUCTIONS,
    version="0.1.0",
)


@lru_cache(maxsize=1)
def get_usage_store() -> UsageStore:
    """Create the shared audit store lazily after MCP initialization."""
    return UsageStore()


@mcp.tool(
    annotations=ToolAnnotations(
        title="DeepSeek usage status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def usage_status(days: Annotated[int, Field(ge=1, le=366)] = 15) -> UsageSummary:
    """Return prompt-free DeepSeek usage totals for the requested period.

    This tool performs no external API call and never returns prompts, source
    contents, responses, or credentials. Candidate operations remain disabled
    until their narrow MCP schemas and end-to-end smoke test are approved.
    """
    return get_usage_store().summarize(days=days)
