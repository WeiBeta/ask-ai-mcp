# Architecture

## Objective

Ask AI MCP lets Claude Desktop and Codex Desktop offload repetitive tool-making
and source preprocessing to DeepSeek while keeping final knowledge work under
Opus/Sol control.

The primary business workload is evidence-based document production from mixed
file bundles. Typical inputs include Markdown, text, PDF, Word, PowerPoint,
Excel, scanned pages, and diagram images. Typical outputs are complex DOCX,
PPTX, and XLSX artifacts with significant table and layout requirements.

## Non-goals

The first product phase does not:

- support macOS;
- expose a general-purpose DeepSeek chat tool;
- let DeepSeek co-author final document prose;
- let unreviewed generated code touch the repository or original source files;
- let DeepSeek decide facts, reconcile evidence, or approve deliverables;
- provide DeepSeek with shell, Git, browser, or unrestricted filesystem access.

## Responsibility planes

### Control plane: Opus/Sol

The host model owns task decomposition, evidence selection, factual judgment,
conflict resolution, document structure, narrative, layout, final verification,
and repository changes.

### Toolsmith plane: DeepSeek

DeepSeek receives bounded specifications, sanitized fixtures, and concise error
reports. It may generate preprocessing or validation code and may repair a
candidate for a bounded number of iterations. Generated code is always
untrusted.

DeepSeek Flash with thinking enabled is the default for bounded code creation.
DeepSeek Pro is an explicit escalation for difficult diagnostics; it is not an
automatic substitute for Sol architecture work.

### Execution plane: isolated candidate runner

Candidate tools execute only in a disposable job directory with:

- no network by default;
- time, memory, process, and output limits;
- allow-listed runtimes and dependencies;
- synthetic or copied fixtures;
- no API keys, Git credentials, or user-profile access;
- an immutable input area and a separate output area.

The initial runner targets pure Python. Office COM and PowerShell automation are
higher-risk adapters and may only run after separate review and promotion.

### Evidence plane: deterministic local processing

Approved tools parse and validate file copies locally. Their output uses a
canonical evidence representation that preserves:

- source file identity and hash;
- page, slide, sheet, cell, paragraph, or object location;
- original value or verbatim text;
- extraction method and tool version;
- confidence, warnings, and validation results.

### Artifact plane: Opus/Sol and specialized renderers

DeepSeek does not edit Office files as a co-author. Opus/Sol uses structured
evidence and approved local tooling to create final DOCX, PPTX, or XLSX files,
then renders and visually validates them.

## End-to-end workflows

### Build a helper tool

1. Opus/Sol defines purpose, input/output contracts, dependencies, fixtures,
   acceptance tests, and prohibited capabilities.
2. DeepSeek generates source code and tests as structured candidate output.
3. Policy scans reject forbidden imports, paths, network access, subprocesses,
   dynamic execution, and other unsafe constructs.
4. The candidate runner executes static checks and tests on fixtures.
5. A concise failure report may be returned to DeepSeek for at most two repair
   rounds.
6. Opus/Sol reviews the final diff, dependencies, tests, and risks.
7. Explicit promotion records the candidate hash and approved capabilities.

### Process a source bundle

1. The host selects an allow-listed input bundle.
2. Deterministic tools inventory files and normalize supported formats.
3. Tables remain structured and keep repeated headers, units, formulas, merged
   regions, and source coordinates.
4. Images and diagrams are interpreted by local OCR/vision or the host model,
   not sent directly to a text-only DeepSeek call.
5. Opus/Sol selects evidence and forms the final facts and narrative.
6. Specialized artifact tooling produces and validates the deliverable.

## MCP surface

The Core public tool set is deliberately narrow:

- `usage_status`
- `workflow_guidance`
- `list_pending_reviews`
- `open_budget_session`
- `budget_status`
- `add_budget_block`
- `close_budget_session`
- `build_helper_tool`
- `review_tool_candidate`
- `approve_tool_candidate`
- `list_registered_tools`
- `run_verified_tool`

There is no arbitrary prompt forwarding tool. Local conversation-budget
operations and candidate build, review, and approval are exposed as separate
narrow operations. Verified execution is
capability-scoped, exact-hash-pinned, dual-approved for output-producing tools,
and closed to real files unless narrow input roots are explicitly configured.

The server uses local STDIO for both desktop clients. Each client may start its
own MCP process, so the audit database and verified-tool registry must support
concurrent access. SQLite runs in WAL mode for shared metadata.

Subagent adds `source_backend_status`, `source_extract`, and
`source_job_status`. Toolsmith and source intelligence remain separate internal
modules even when the same local Qwen backend serves both. H3 remains a separate
GPU generation capability.

## Storage

- Source code: `C:\Dev\ask-ai-mcp`
- Python environment: `C:\Dev\ask-ai-mcp\.venv`
- Per-user state: `%LOCALAPPDATA%\AskAIMCP`
- Usage database: `%LOCALAPPDATA%\AskAIMCP\usage.db`
- Candidate jobs: `%LOCALAPPDATA%\AskAIMCP\jobs`
- Verified tool registry: `%LOCALAPPDATA%\AskAIMCP\registry`
- Verified execution runs: `%LOCALAPPDATA%\AskAIMCP\runs`

Secrets will be stored in Windows Credential Manager. Environment variables may
be supported for isolated development and CI, but secrets are never committed.

## Audit model

The audit store records timestamps, client identity, task kind, model tier,
thinking mode, token usage, cache hits, estimated cost, latency, retry count,
opaque budget/lifecycle IDs, request/response character counts, structural
lifecycle character and UTF-8 byte sizes, test outcome, candidate hash, and
final promotion decision. Actual API token counts and structural sizes remain
separate measurements.

It does not record API keys, full prompts, full source contents, or ordinary
model responses by default.

Explicit shadow testing uses a separate immutable replay store. It may retain
sanitized tool specifications and parsed candidate code because those contents
are the evaluation corpus; it never changes the prompt-free audit contract.
