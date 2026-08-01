# Ask AI MCP

Private, Windows-only MCP server for Claude Desktop and Codex Desktop.

Ask AI MCP gives top-tier host models a constrained DeepSeek assistant for two
purposes only:

1. Build and repair bounded preprocessing, extraction, conversion, and format
   validation tools.
2. Produce source-faithful structured data with traceable source locations.

DeepSeek is never a document co-author. It must not draft final report prose,
decide facts, resolve source conflicts, or produce delivery-ready conclusions.
Opus/Sol remains responsible for evidence selection, reasoning, narrative,
layout, repository changes, and final acceptance.

## Current status

Phase 0 provides the Windows package skeleton, strict task and audit models,
runtime policy, DeepSeek V4 cost estimation, a prompt-free SQLite usage store,
and an intentionally narrow MCP surface.

Only `usage_status` is currently exposed. Candidate generation and execution
stay disabled until the isolated runner and review gates are implemented.

## Development

Prerequisites:

- Windows 11
- system Python 3.13
- system `uv`
- PowerShell 7

From `C:\Dev\ask-ai-mcp`:

```powershell
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run fastmcp inspect src/ask_ai_mcp/server.py --format mcp
```

The local MCP entry point is:

```text
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp.exe
```

## Documentation

- `docs/ARCHITECTURE.md`: system roles, data flow, isolation, and storage
- `docs/DEEPSEEK_POLICY.md`: mandatory delegation and content boundaries
- `docs/DESKTOP_SETUP.md`: Claude Desktop and Codex Desktop connection guide
- `docs/ROADMAP.md`: staged implementation and exit criteria
- `AGENTS.md`: repository rules for future model-driven development

## Provenance

This private repository began as a fork of `jimmyloi/ask-ai-mcp`. The upstream
single-file proof of concept is retained as `mcp_server.py` for reference only;
it is excluded from the rebuilt package and is not a supported entry point.

The upstream repository did not declare a license when this fork was created.
Keep this derivative private and internal until reuse rights are clarified.
