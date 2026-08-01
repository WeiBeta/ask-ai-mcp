# Roadmap

Current implementation status: phases 0 and 1 are complete, including the
real opt-in synthetic Flash smoke request. Desktop configuration should remain
limited to the read-only status tool until the later safety gates are complete.

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

- Create per-job directories and immutable fixture inputs.
- Add Python AST and dependency policy checks.
- Add runtime, memory, process, and output limits.
- Implement at most two automated repair rounds.
- Return candidate patch, tests, and risk report for Sol review.

Exit criteria: unsafe fixtures are rejected and safe candidates cannot escape
the job directory or alter repository files.

## Phase 3: document preprocessing toolkit

- Inventory and hash mixed source bundles.
- Support DOCX, PPTX, XLSX, PDF, Markdown, and text extraction.
- Add table-aware canonical evidence records and provenance.
- Add rendering and structural validators.
- Add OCR and diagram preprocessing adapters.

Exit criteria: fixture bundles round-trip into traceable evidence records with
no modification of the originals.

## Phase 4: Office fidelity adapters

- Evaluate installed Microsoft Office and conversion capabilities.
- Add controlled legacy DOC/PPT/XLS conversion.
- Add Word pagination, PowerPoint rendering, and Excel recalculation checks.
- Require explicit promotion for every COM/PowerShell-capable tool.

Exit criteria: approved tools process copies and produce independently
verifiable outputs without arbitrary host automation.

## Phase 5: desktop integration and trial

- Connect local STDIO to Codex Desktop.
- Package the same server for Claude Desktop on Windows.
- Configure tool approval and server instructions.
- Run the fifteen-day trial and review quality, cost, latency, and failure data.
- Set budget policy only after observing real workloads.

Exit criteria: both clients use the same policy and audit store, and trial data
supports a decision on production limits and model routing.
