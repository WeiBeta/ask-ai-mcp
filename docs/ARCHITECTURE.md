# Architecture

## Objective

Ask AI MCP lets Claude Desktop and Codex Desktop offload bounded tool-making,
coding, review, perception and local media work to restricted workers while
keeping final judgment and repository authority under Opus/Sol control.

The primary business workload is evidence-based document production from mixed
file bundles. Typical inputs include Markdown, text, PDF, Word, PowerPoint,
Excel, scanned pages, and diagram images. Typical outputs are complex DOCX,
PPTX, and XLSX artifacts with significant table and layout requirements.

## Non-goals

The first product phase does not:

- support macOS;
- expose a general-purpose model chat tool;
- let a Worker co-author final document prose;
- let unreviewed generated code touch the repository or original source files;
- let a Worker decide facts, reconcile evidence, or approve deliverables;
- provide a Worker with shell, Git, browser, or unrestricted filesystem access.

## Responsibility planes

### Control plane: Opus/Sol

The host model owns task decomposition, evidence selection, factual judgment,
conflict resolution, document structure, narrative, layout, final verification,
and repository changes.

### Worker plane: provider-neutral bounded roles

Workers receive only the bounded input contract of their role. Stable roles are
`toolsmith`, `coding`, `review`, `perception`, and `video`; provider and model
names are route metadata selected behind that role. Generated code and model
findings are always untrusted.

Toolsmith repair counts, Coding target files, Review snapshots, Perception input
roots and H3 workspace boundaries remain capability-specific. A route failure
never authorizes automatic paid retry, model fallback or a wider input scope.

### Composition and accounting planes

Each executable name is a thin compatibility profile composed from explicit
capabilities. Profiles do not own provider or pricing logic. Core, Coding and
Review share one provider-neutral accounting composition over the existing
SQLite usage store:

- entitlement checks answer whether an already configured subscription route
  is available inside its current windows and model caps;
- the immutable ledger appends prompt-free usage records and summarizes them;
- approval policy separately answers whether private-source export, a new or
  funded subscription, allow-list expansion, or a paid retry needs explicit
  user authorization.

Accounting is an internal module, not another MCP server or another state
ledger. Historical direct-provider CNY fields remain compatibility data only.

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

A Worker does not edit Office files as a co-author. Opus/Sol uses structured
evidence and approved local tooling to create final DOCX, PPTX, or XLSX files,
then renders and visually validates them.

## End-to-end workflows

### Build a helper tool

1. Opus/Sol defines purpose, input/output contracts, dependencies, fixtures,
   acceptance tests, and prohibited capabilities.
2. The selected toolsmith Worker generates source code and tests as structured candidate output.
3. Policy scans reject forbidden imports, paths, network access, subprocesses,
   dynamic execution, and other unsafe constructs.
4. The candidate runner executes static checks and tests on fixtures.
5. A concise failure report may be returned to the same Worker for at most two repair
   rounds.
6. Opus/Sol reviews the final diff, dependencies, tests, and risks.
7. Explicit promotion records the candidate hash and approved capabilities.

### Process a source bundle

1. The host selects an allow-listed input bundle.
2. Deterministic tools inventory files and normalize supported formats.
3. Tables remain structured and keep repeated headers, units, formulas, merged
   regions, and source coordinates.
4. Images and diagrams are interpreted by local OCR/vision or the host model,
   not sent directly to an unrelated text-only Worker route.
5. Opus/Sol selects evidence and forms the final facts and narrative.
6. Specialized artifact tooling produces and validates the deliverable.

## MCP surface

The Core public tool set is deliberately narrow:

- `usage_status`
- `workflow_guidance`
- `list_pending_reviews`
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

Version 0.13.7 divides local state into three retention classes:

- `archive`: `usage.db` and `code-review/review.db`; limits are maintenance
  thresholds, and no accounting, finding, adjudication, or outcome row is silently
  deleted;
- `protected`: the verified registry and any active, incomplete, modified,
  unexported, unadjudicated, reparse-point, or explicitly retained benchmark unit;
- `rolling`: complete receipt-bound Coding/Review/Toolsmith/Source jobs, verified
  runs, smoke runs, new hash-directory staged patches, and replay capsules.

Rolling eviction is oldest-first and whole-unit. Replay is capped at 100 MiB and
encrypted wire evidence keeps its independent 100 MiB per specialist-pool cap.
Legacy staged-patch file pairs remain readable but protected; new pairs publish as
one hash-named directory so deletion cannot mix patch and receipt generations.
`usage_status` reports only content-free counters and never exposes these roots.

Secrets will be stored in Windows Credential Manager. Environment variables may
be supported for isolated development and CI, but secrets are never committed.

## Audit model

The audit store records timestamps, client identity, task kind, model tier,
thinking mode, token usage, cache hits, estimated cost, latency, retry count,
opaque audit-scope/lifecycle IDs, request/response character counts, structural
lifecycle character and UTF-8 byte sizes, test outcome, candidate hash, and
final promotion decision. Actual API token counts and structural sizes remain
separate measurements.

Normal SQLite, protocol, and audit records do not store API keys, full prompts,
full source contents, or ordinary model responses. Version 0.13.1 keeps Coding
and Review wire evidence in a separate, UID-linked AES-256-GCM frame store. Its
key is held by Windows Credential Manager, authorization material is never
captured, and retention evicts whole UID bundles rather than mixing calls.

Explicit shadow testing uses a separate immutable replay store. It may retain
sanitized tool specifications and parsed candidate code because those contents
are the evaluation corpus; it never changes the prompt-free audit contract.
