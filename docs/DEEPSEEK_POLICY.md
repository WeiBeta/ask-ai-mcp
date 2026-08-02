# DeepSeek delegation policy

## Absolute rule

DeepSeek must not directly draft or co-author final document prose.

This is enforced through narrow MCP schemas and server policy. It is not merely
a recommendation in a system prompt, and the first version has no override.

Rejected task classes include:

- report or proposal body drafting;
- chapter generation or long-form continuation;
- executive summaries and final conclusions;
- factual arbitration and conflict resolution;
- final wording intended for direct delivery;
- stylistic co-authoring of DOCX or PPTX content.

## Allowed roles

### Toolsmith

DeepSeek may create or repair bounded tools for:

- DOCX/PPTX/XLSX/PDF parsing;
- table extraction, normalization, conversion, and validation;
- file inventory, hashing, deduplication, and comparison;
- OCR and diagram preprocessing pipelines;
- formula, style, pagination, overflow, and structural checks;
- fixture, mock, unit-test, and regression-test generation.

### Source structuring worker

DeepSeek may return schema-constrained, source-faithful data for:

- heading and field classification;
- table-header mapping;
- unit and date normalization;
- verbatim extraction with source references;
- structural anomaly detection.

Source-structuring output must not introduce new factual claims. Long narrative
fields are rejected, and provenance is required.

### Bounded coding assistant

DeepSeek Flash Thinking may assist Sol with:

- single-purpose parsers and validators;
- mechanical transformations;
- tests and fixtures;
- local bug diagnosis from explicit errors;
- candidate patches for narrow interfaces.

DeepSeek Pro may be requested explicitly for difficult OOXML cases, cross-module
diagnosis, or repeated format failures. Pro does not own architecture, security,
integration, promotion, or release decisions.

## Model routing

- Mechanical classification or normalization: V4 Flash, thinking disabled.
- Code or test generation: V4 Flash, thinking enabled, effort high.
- Candidate repair: V4 Flash, thinking enabled, effort high.
- Difficult diagnostic escalation: V4 Pro, thinking enabled, explicitly chosen
  by Opus/Sol.
- Final narrative or factual work: rejected rather than routed.

There is no automatic Pro escalation. After two failed Flash repair rounds,
control returns to Opus/Sol.

## Candidate-code rules

Generated code may not:

- write into the repository;
- modify original source files;
- access paths outside a job workspace;
- use network access unless a separately reviewed tool explicitly requires it;
- read environment variables or credential stores;
- start arbitrary processes;
- install packages dynamically;
- use `eval`, `exec`, dynamic imports, or encoded payload execution;
- persist after its job finishes.

A generated candidate is promoted only after tests pass and Opus/Sol explicitly
approves the exact hash. Editing a promoted tool invalidates approval.

## Data minimization

Tool-building requests should contain contracts and synthetic fixtures instead
of real source bundles. If a real edge case is necessary, provide the smallest
sanitized excerpt that reproduces it.

Logs use hashes and structural metadata instead of filenames or content where
possible. DeepSeek API request and response bodies are not archived by default.
Thinking content is discarded; it is never returned to the host or written to
the audit store.

## Trial metrics

The initial fifteen-day trial tracks:

- calls and cost by task kind, client, model, and pricing band;
- cache hit, miss, output, and reasoning tokens;
- candidate first-pass success rate;
- repair rounds and escalation rate;
- static-policy rejection reasons;
- approved, modified, and rejected candidate counts;
- verified-tool execution and validation failures.

The current MCP surface does not expose source-structuring execution yet.
`build_helper_tool` must not be used as a substitute channel for source text.

## Delegation threshold and approval completion

Delegate only mechanically verifiable work that is expected to require at
least three implementation/debugging rounds or will produce a registered,
reusable tool. Otherwise the controller performs the work directly.

A candidate is approval-ready only when its patch is readable and unobfuscated,
its tests cover every declared acceptance test, every dependency is allowed,
static analysis has no error-level finding, and the candidate hash matches the
revalidated review bundle. Rejection sends control back to specification
revision and a fresh build; old candidate bytes are never edited in place.

Synthetic-only candidates need one controller review. Copied-input candidates
need one controller review and an audit record. Dedicated-output or Pro-built
candidates require independent Claude and Codex review before real-file
execution is enabled. Server-side dual-review enforcement is planned with the
verified execution surface.
