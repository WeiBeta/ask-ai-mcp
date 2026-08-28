# Ask AI MCP

Private, Windows-only MCP server for Claude Desktop and Codex Desktop.

Ask AI MCP gives top-tier host models constrained external-model workers for two
purposes only:

1. Build and repair bounded preprocessing, extraction, conversion, and format
   validation tools.
2. Produce source-faithful structured data with traceable source locations.

An external worker is never a document co-author. It must not draft final report prose,
decide facts, resolve source conflicts, or produce delivery-ready conclusions.
Opus/Sol remains responsible for evidence selection, reasoning, narrative,
layout, repository changes, and final acceptance.

## Current status

Phases 0 through 2 are complete internally. The project includes Windows Credential
Manager storage and provider adapters with schema-constrained JSON candidates,
response-body-free errors, prompt-free
usage accounting, a successfully completed synthetic Flash smoke request, and
a digest-pinned Docker/WSL 2 candidate runner.

The lifecycle permits one build plus at most two policy-routed repair rounds,
returns a compact review summary by default, and requires explicit hash-matched
promotion. Subscription-backed calls use a shared provider-neutral ledger; USD 2/day
is reported as a utilization pace, not enforced as a hard daily cap. The development
compatibility Full surface exposes fifteen narrow tools: four loopback-only
MiniMax H3/ComfyUI video tools, three pre-deployment multimodal source tools,
usage and lifecycle economics, on-demand
workflow guidance, cross-desktop pending-review discovery, candidate build,
review, approval, registry listing, and verified execution. Verified execution
requires a standard callable contract, exact registry hash, dual Claude/Codex
approval for output-producing tools, Docker isolation, and explicitly
configured narrow input roots. No real input root is enabled by default.

Version 0.7.0 adds fixed OpenCode Go coding/perception providers and a three-tool
Perception-only entry while keeping detailed protocol text out of always-loaded
tool descriptions. Controllers request only the workflow topic they need. Review
summaries and the local pending queue join creator, exact full-review
attestations, approval identities, blocking reasons, and the next action without
returning candidate content. Usage reporting keeps actual API token counts
separate from structural character and UTF-8 byte measurements.

Version 0.7.1 adds a bounded three-scope visual extraction contract without
adding MCP tools: compact structure indexes, separate topology, and selected
details based on explicit identifiers plus a deterministic normalized crop.

Version 0.8.0 adds an independent, opt-in Coding Review MCP and an
auditable OpenCode Go ledger with shared rolling windows, per-model monthly
caps, DeepSeek peak/off-peak rates, and subscription isolation. Review is not
loaded by Core, Subagent, Perception, H3, or Full.

Version 0.9.0 adds a separate three-tool Coding MCP for bounded candidate
patches, fixes Coding to DeepSeek V4 Flash or GLM-5.3-Flash, and narrows Review
to DeepSeek V4 Pro, GLM-5.3, or Kimi K3. Both request the provider's highest
`max` reasoning setting, share one UID-keyed OpenCode Go ledger, and never write
the source repository. Direct DeepSeek routing is suspended by default.

Version 0.9.1 fixes Coding repository discovery so the independent Coding MCP
reads `ASK_AI_MCP_CODING_REPOSITORIES` rather than the Review allow-list. Both
modules remain deny-by-default and may register multiple explicit Git roots.

Version 0.9.2 adds model-specific Review reasoning/output policies and explicit
prompt-free failure codes for reasoning-budget exhaustion, output truncation,
invalid structured responses, provider errors, and internal failures. Failed
reviews never retry automatically.

Version 0.10.0 adds a fourth Review tool that validates and atomically seals a
bounded patch in a project-specific external staging root. Paid patch review
accepts only a matching immutable patch/receipt pair pinned by both SHA-256
values; incomplete, altered, or root-mismatched pairs fail before submission.

Version 0.12.0 centralizes OpenCode Go generation policy and raises thinking-heavy
Core, Coding, and Review calls to a 131,072-token total generation cap. Review
preflight now blocks only context-boundary risk with a deterministic advisory
partition plan and zero provider calls; provider failures expose content-free
machine-readable subclasses. Static hash-bound repairs retain their 4,096-token cap.

Version 0.12.1 keeps DeepSeek V4 Flash and GLM-5.3-Flash as the preferred Coding
routes and adds DeepSeek V4 Pro, GLM-5.3, and Kimi K3 as explicit advanced
candidate routes for complex Coding tasks. The advanced routes are never selected
as an automatic fallback and retain the same frozen-input, external-candidate,
strict-hash, single-call boundary.

Version 0.12.2 replaces the former 10/15/30-minute remote generation timeouts with
one shared bounded policy: asynchronous Coding, Review, and remote Perception jobs
allow a two-hour read window, while the synchronous Core toolsmith allows one hour.
Connect, upload, and pool waits remain independently bounded. Job status distinguishes
local worker activity from confirmed upstream progress, and prompt-free timeout audits
record phase, elapsed time, configured policy, and whether provider usage was observed.
No timeout failure is retried or rerouted automatically.

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

The recommended always-on MCP entry point is:

```text
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp-core.exe
```

Seven explicit profiles share the same codebase:

- `ask-ai-mcp-core.exe` registers the twelve non-video tools only.
- `ask-ai-mcp-subagent.exe` registers Core plus three multimodal source tools, but no H3.
- `ask-ai-mcp-perception.exe` registers only the three multimodal source tools.
- `ask-ai-mcp-h3.exe` registers only the four local video and ComfyUI tools.
- `ask-ai-mcp-review.exe` registers only four read-only code-review/staging tools and is opt-in.
- `ask-ai-mcp-coding.exe` registers only three bounded coding-candidate tools and is opt-in.
- `ask-ai-mcp-full.exe` registers all fifteen tools, including source and H3 adapters.

The legacy `ask-ai-mcp.exe` entry point defaults to Full and accepts
`ASK_AI_MCP_PROFILE=core|subagent|perception|h3|review|coding|full`. New desktop deployments should use the explicit
profile executable so a missing environment variable cannot silently change the
advertised tool surface.

Build the four credential-free Windows migration packages with:

```powershell
pwsh -NoProfile -File .\scripts\build_migration_packages.ps1
```

The Core, Subagent, H3, and Full archives contain only the MCP wheel, locked dependencies,
configuration templates, verification scripts, hashes, and GPT handoff material.
No archive contains ComfyUI, Qwen, model weights, local application state, business
files, desktop personal configuration, or credentials.

The H3 runtime, model weights, and generated media are deliberately external to
this repository. On the current workstation they live under
`C:\AI\ComfyUI`; only the small loopback adapter, tests, and operating
documentation are versioned here.

The four H3 tools automatically run the bounded PowerShell launcher when the
loopback backend is unavailable, then wait for ComfyUI readiness. The launcher
can also be run manually:

```powershell
pwsh -NoProfile -File .\scripts\start_comfyui_h3.ps1
```

Reference frames belong in
`C:\Users\user\Documents\AskAI-Exchange\H3-Workspace\inputs`; generated videos
are written to the sibling `outputs` directory. This narrow shared workspace is
writable by both Codex and Claude Desktop; the runtime and model weights remain
isolated under `C:\AI\ComfyUI`.

## Documentation

- `docs/ARCHITECTURE.md`: system roles, data flow, isolation, and storage
- `docs/DEEPSEEK_POLICY.md`: mandatory delegation and content boundaries
- `docs/DEEPSEEK_CLIENT.md`: API contract, credential workflow, and safeguards
- `docs/PRICING.md`: base/peak pricing schedule and auditable cost metadata
- `docs/ISOLATION.md`: Docker/WSL candidate sandbox and workspace boundary
- `docs/CANDIDATE_LIFECYCLE.md`: repair, review, and hash-pinned promotion gates
- `docs/MCP_SURFACE.md`: public tool contracts and desktop identity boundary
- `docs/CODE_REVIEW_ZH-CN.md`: isolated review workflow, accounting, and blind evaluation
- `docs/MCP_SMOKE.md`: first billed build-review-approval evidence
- `docs/H3_HANDOFF_ZH-CN.md`: verified H3 deployment, model hashes, MCP contract, and next-session handoff
- `docs/QWEN_SUBAGENT_PREDEPLOY_ZH-CN.md`: replay capture, source contract, and exact Qwen integration stop point
- `docs/OPENCODE_GO_PREDEPLOY_ZH-CN.md`: OpenCode Go coding and Qwen3.8 Max perception routing
- `operator-archive/`: Chinese module reference plus a generator for Git-ignored, physical-host-only runtime and GUI configuration snapshots
- `ask-ai-mcp-replay shadow-qwen --lifecycle-id <UUID>` replays a captured tool spec through local Qwen and records self-test plus reference-test evidence without treating DeepSeek tests as a gold oracle.
- `docs/DESKTOP_SETUP.md`: Claude Desktop and Codex Desktop connection guide
- `docs/CONTROLLER_PROMPTS.md`: shared delegation rules and desktop adaptations
- `docs/USER_MANUAL_ZH-CN.md`: Simplified Chinese user, handoff, backup, and migration manual
- `docs/ROADMAP.md`: staged implementation and exit criteria
- `AGENTS.md`: repository rules for future model-driven development

## Provenance

This private repository began as a fork of `jimmyloi/ask-ai-mcp`. The upstream
single-file proof of concept is retained as `mcp_server.py` for reference only;
it is excluded from the rebuilt package and is not a supported entry point.

The upstream repository did not declare a license when this fork was created.
Keep this derivative private and internal until reuse rights are clarified.
