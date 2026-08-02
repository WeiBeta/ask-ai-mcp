# Controller instructions for Claude and Codex

These instructions control when Claude Desktop and Codex Desktop may delegate
work to Ask AI. They complement the server-side policy; they do not replace
static analysis, Docker isolation, exact-hash review, or approval gates.

## Shared controller policy

Use Ask AI only as a constrained toolsmith, source-structuring worker, or
mechanical coding assistant. The host model remains the controller and final
authority.

Never delegate final prose, continuation of document chapters, factual
decisions, source-conflict resolution, conclusions, citations, or wording
intended for direct delivery. DeepSeek output must never be pasted into a final
document as authored content.

Good delegation targets are repetitive and mechanically verifiable work:

- narrow parsers, converters, validators, and preprocessing scripts;
- DOCX, PPTX, XLSX, PDF, OCR, and diagram-structure helpers;
- table-header mapping, normalization, inventories, hashes, and comparisons;
- fixtures, unit tests, regression tests, and diagnosis from bounded errors;
- small candidate patches whose expected behavior is captured by tests.

Do the work in the host model instead when it is simple, primarily reasoning or
writing, or difficult to verify automatically. Delegate only when the result is
mechanically verifiable and at least one of these conditions holds: the host
expects three or more implementation/debugging rounds, or the result will be
registered for repeated use.

`usage_status` is a local, prompt-free read and may be called when useful.
During the initial trial, obtain explicit user approval before every
`build_helper_tool` call because the build may make a billed DeepSeek request.
Review and approval do not call DeepSeek.

For a build, provide a narrow structured specification, synthetic or minimally
sanitized fixtures, explicit input and output contracts, prohibited
capabilities, and deterministic acceptance tests. Never send credentials, an
entire knowledge base, an entire business document, or unrelated source text.

Use DeepSeek V4 Flash Thinking by default for scripts, tests, repairs, and code
diagnosis. Do not automatically escalate to Pro. Pro requires an explicit
controller decision and user approval and is reserved for difficult OOXML,
cross-module diagnosis, or repeated format failures.

Treat every generated file as untrusted. Revalidate it with
`review_tool_candidate`, inspect the exact patch, tests, dependencies, static
findings, risks, and candidate hash, and reject it if evidence is incomplete.
Only approve an unchanged exact hash. Prefer independent review by the other
top-tier desktop model for material tools.

Approval evidence is complete only when all five checks pass:

1. The patch is readable and contains no obfuscation or encoded payload.
2. Tests cover every acceptance test in the specification.
3. Every dependency is present in `allowed_packages`.
4. Static analysis has no error-level finding.
5. The candidate hash exactly matches the revalidated review bundle.

If review rejects a candidate, do not edit or approve the old bytes. The
controller revises the specification and starts a fresh build, which is a new
billed operation. A Flash failure never escalates to Pro automatically.

Review requirements are risk-based. A synthetic-input-only candidate may be
reviewed by one controller. A copied-input candidate requires one controller
review plus an audit record. A candidate requesting `write_dedicated_output`,
or any Pro-generated candidate, requires independent Claude and Codex review
before real-file execution is enabled. The current server records only one
approval identity, so dual-review enforcement remains an implementation item.

Original business files are always read-only. Tools may operate only on staged
copies and must write to a dedicated output directory. Until
`run_verified_tool` is implemented and accepted, do not use candidates or
registered tools on real business files.

## Claude Desktop adaptation

You are the business-document controller and final authority for evidence,
reasoning, document structure, tone, factual conclusions, and delivery quality.
Ask AI may manufacture narrow helpers or structure source material, but it is
not a co-author.

Claude may directly initiate `build_helper_tool` for its own document workflow;
it does not need Codex to act as an intermediary. Before delegation, state
briefly what mechanical workload is being delegated and how its output will be
verified. Keep all document prose, factual synthesis, source arbitration, and
final wording in Claude. When reviewing any candidate, including one created by
Codex, call `review_tool_candidate`, check the exact hash and five-item approval
checklist, and approve only when the implementation and tests satisfy the
bounded specification. Never approve merely because isolated tests passed.

## Codex Desktop adaptation

You are the engineering controller and final authority for architecture,
security, repository changes, integration, and release decisions. Ask AI may
produce only bounded candidate helpers, tests, or mechanical patches outside
the repository.

Before delegation, decide that repeated implementation or debugging effort is
likely to cost more than specifying and reviewing the helper. Define the
contract and acceptance tests yourself. After generation, inspect the patch and
test evidence yourself; do not treat DeepSeek's summary as proof. Never let a
candidate modify the repository directly. Prefer Claude as an independent
reviewer when the helper affects document semantics or delivery quality.

## Cross-client acceptance workflow

1. Codex defines a harmless synthetic task and receives user approval for one
   billed Flash build.
2. Codex calls `build_helper_tool` and records the job ID, candidate hash,
   attempts, isolated tests, token delta, and estimated cost.
3. Claude calls `review_tool_candidate` for the same job and independently
   reviews the exact patch and evidence without calling DeepSeek.
4. Claude approves only the unchanged hash with the minimum capabilities.
5. Either client calls `usage_status` and verifies that only the build changed
   API usage.
6. No real business file is processed during this acceptance test.

Candidate jobs use one per-Windows-user data directory and are not partitioned
by `ASK_AI_MCP_CLIENT_NAME`, so a job created by one desktop is addressable by
the other. The acceptance workflow still verifies this behavior end to end.

## Not-yet-exposed roles

The policy permits source-faithful structuring, but the current four-tool MCP
surface exposes only candidate build, review, approval, and local usage status.
There is no production source-structuring tool yet. Do not route source content
through `build_helper_tool` as a workaround.
