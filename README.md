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

Phases 0 through 2 are complete internally. The project includes Windows Credential
Manager storage and an internal DeepSeek V4 client with schema-constrained JSON
candidates, explicit Pro escalation, response-body-free errors, prompt-free
usage accounting, a successfully completed synthetic Flash smoke request, and
a digest-pinned Docker/WSL 2 candidate runner.

The lifecycle permits one build plus at most two policy-routed repair rounds,
returns a compact review summary by default, and requires explicit hash-matched
promotion. Per-conversation budget sessions start with CNY 5 for Flash and CNY
0 for Pro; either model is extended only in fixed CNY 5 blocks. The development
MCP surface exposes ten narrow tools: usage, four local budget operations,
candidate build, review, approval, registry listing, and verified execution. Verified execution
requires a standard callable contract, exact registry hash, dual Claude/Codex
approval for output-producing tools, Docker isolation, and explicitly
configured narrow input roots. No real input root is enabled by default.

The first full billed lifecycle smoke passed on 2026-08-02 in one Flash call:
the generated synthetic helper passed four isolated tests, was reviewed, and
was registered under an exact hash with synthetic-input capability only.

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
- `docs/DEEPSEEK_CLIENT.md`: API contract, credential workflow, and safeguards
- `docs/PRICING.md`: base/peak pricing schedule and auditable cost metadata
- `docs/ISOLATION.md`: Docker/WSL candidate sandbox and workspace boundary
- `docs/CANDIDATE_LIFECYCLE.md`: repair, review, and hash-pinned promotion gates
- `docs/MCP_SURFACE.md`: public tool contracts and desktop identity boundary
- `docs/MCP_SMOKE.md`: first billed build-review-approval evidence
- `docs/DESKTOP_SETUP.md`: Claude Desktop and Codex Desktop connection guide
- `docs/CONTROLLER_PROMPTS.md`: shared delegation rules and desktop adaptations
- `docs/ROADMAP.md`: staged implementation and exit criteria
- `AGENTS.md`: repository rules for future model-driven development

## Provenance

This private repository began as a fork of `jimmyloi/ask-ai-mcp`. The upstream
single-file proof of concept is retained as `mcp_server.py` for reference only;
it is excluded from the rebuilt package and is not a supported entry point.

The upstream repository did not declare a license when this fork was created.
Keep this derivative private and internal until reuse rights are clarified.
