# Roadmap

Current implementation status: phases 0 through 2 are complete, including the
real opt-in synthetic Flash smoke request and the four-tool desktop surface.
Verified execution and real-file processing remain disabled.

## Phase 0: policy and skeleton

- Establish Windows-only package layout and locked dependencies.
- Encode the no-final-prose boundary in repository rules and runtime policy.
- Define strict request, candidate, audit, and usage schemas.
- Add local SQLite audit storage with prompt-free logging.
- Add offline tests; no real API key is required.

Exit criteria: policy tests pass and the MCP process starts without writing to
stdout outside the protocol.

## Phase 1: DeepSeek toolsmith client

- Add Windows Credential Manager integration.
- Add DeepSeek V4 Flash/Pro routing.
- Generate structured candidate files through JSON output.
- Record token usage, cache hits, latency, retries, and estimated cost.
- Keep candidate generation read-only with respect to the repository.

Exit criteria: mocked integration tests pass and one real opt-in smoke request
can generate a harmless candidate without logging secrets.

## Phase 2: isolated candidate runner

Selected backend: all-users Docker Desktop with WSL 2 Linux containers.

- [x] Create per-job directories and immutable fixture inputs.
- [x] Add Python AST and dependency policy checks.
- [x] Add runtime, memory, process, and output limits.
- [x] Implement at most two automated repair rounds.
- [x] Return candidate patch, tests, and risk report for Sol review.
- [x] Require explicit job ID and candidate hash for local promotion.

Exit criteria: unsafe fixtures are rejected and safe candidates cannot escape
the job directory or alter repository files.

Status: completed on 2026-08-02. The narrow MCP schemas and first billed
build-review-approval smoke also passed. Verified real-file execution remains
disabled.

## Phase 3: verified execution and asset inventory

- Add `list_registered_tools` with hash, version, capabilities, approval time,
  and execution counters.
- Add `run_verified_tool` for exact-hash registry entries only.
- Stage read-only input copies and write only to a dedicated output directory.
- Persist an execution evidence manifest, hashes, warnings, and validation
  results.
- Enforce independent Claude/Codex review for Pro or dedicated-output tools.

Exit criteria: an approved synthetic helper can process a copied fixture and
produce independently verifiable output without changing the original.

## Phase 4: document preprocessing toolkit

- Inventory and hash mixed source bundles.
- Support DOCX, PPTX, XLSX, PDF, Markdown, and text extraction.
- Add table-aware canonical evidence records and provenance.
- Add rendering and structural validators.
- Add OCR and diagram preprocessing adapters.

Exit criteria: fixture bundles round-trip into traceable evidence records with
no modification of the originals.

## Phase 5: Office fidelity adapters

- Evaluate installed Microsoft Office and conversion capabilities.
- Add controlled legacy DOC/PPT/XLS conversion.
- Add Word pagination, PowerPoint rendering, and Excel recalculation checks.
- Require explicit promotion for every COM/PowerShell-capable tool.

Exit criteria: approved tools process copies and produce independently
verifiable outputs without arbitrary host automation.

## Phase 6: desktop integration and trial

- [x] Connect local STDIO to Codex Desktop.
- [x] Connect the same server to Claude Desktop on Windows.
- [x] Configure tool approval and controller instructions.
- Run the fifteen-day trial and review quality, cost, latency, and failure data.
- Set budget policy only after observing real workloads.

Exit criteria: both clients use the same policy and audit store, and trial data
supports a decision on production limits and model routing.
