# Roadmap

Current implementation status: phases 0 through 2 are complete, including the
real opt-in synthetic Flash smoke request. Version 0.9.0 exposes seven explicit
MCP profiles (Core 8, Perception 3, Subagent 11, H3 4, Full 15, independent
Review 4, and independent Coding 3), subscription ledgers, compact/full review,
verified execution, and the OpenCode Go dual-layer usage ledger. Cross-client
Phase 3 acceptance and explicit real-input-root configuration remain pending.
Version 0.9.1 separates the Coding and Review repository environment catalogs
while preserving explicit deny-by-default Git-root allow-lists.
Version 0.9.2 classifies Review output-budget failures before JSON parsing and
records each model's actual reasoning effort and output cap. Token/cost preflight
and explicit frozen-file shards remain a later P1 design item.
Version 0.9.3 adds prompt-free Review validation-stage diagnostics without
relaxing the response schema, retry policy, or model budgets.
Version 0.9.4 makes all six canonical Review categories explicit in prompt v2.
The Go Chat Completions endpoint remains in documented JSON-object mode because
its official endpoint matrix does not promise strict JSON Schema enforcement;
the separate OpenCode session structured-output API includes validation retries
and is therefore outside the Review module's single-call policy.
Version 0.9.5 makes the immutable changed-file allow-list a separate exact JSON
array in prompt v3. Findings must copy one entry verbatim; Git `a/` and `b/`
prefixes, case changes, similar prefixes, and parent traversal remain rejected.
Version 0.10.0 adds atomic, project-specific Review patch staging. A validated
patch is committed with a content-free receipt and both hashes must match before
patch-only submission can reach the provider.
Version 0.12.2 centralizes bounded provider timeout policy. Async Coding, Review,
and remote Perception use a two-hour read window; synchronous Core uses one hour.
Status and prompt-free audit metadata distinguish local worker activity from
confirmed upstream progress. Local Qwen slot polling, ComfyUI job polling, and
sandbox execution deadlines remain separate mechanisms. Streaming progress is a
future opt-in experiment, not an implicit retry or protocol relaxation.

Version 0.12.3 aligns injected timeout overrides with the public status contract
and adds direct regression coverage for successful Review metadata and local
Qwen requests that remain active after a client timeout.

Version 0.13.0 is an isolated test-major foundation, not the deployed production
baseline. It defines immutable executable profiles over explicit capabilities,
introduces provider-neutral worker-role routing names, and splits accounting
into entitlement checks, an immutable usage ledger, and user-approval policy
over the existing SQLite store. The seven executable names, tool order and
parameter schemas remain compatible with 0.12.3. Production stays on 0.12.3
until 0.13 passes full offline verification, external frozen-diff Review and
explicit physical gray testing; merge and deployment require a separate user
decision.

Version 0.13.2 adds Grok 4.6, tiered local pricing, and non-executing route
recommendations while preserving explicit model selection and no paid retry.
Version 0.13.3 maps Grok's provider-native maximum reasoning effort to `xhigh`
and rejects estimated input at or above its 200K high-price band before any
Coding or Review provider call.
Version 0.13.1 adds UID-correlated encrypted wire evidence for Coding/Review,
stream-boundary transport diagnostics, and a local operator-only idempotent
reconciliation receipt for dashboard-confirmed billed transport failures. It
does not add a prompt-forwarding, decrypt, retry, or generic ledger-edit MCP tool.

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
build-review-approval smoke also passed.

## Phase 3: verified execution and asset inventory

- [x] Add `list_registered_tools` with hash, version, capabilities, approval time,
  and execution counters.
- [x] Add `run_verified_tool` for exact-hash registry entries only.
- [x] Stage read-only input copies and write only to a dedicated output directory.
- [x] Persist an execution evidence manifest, hashes, warnings, and validation
  results.
- [x] Enforce independent Claude/Codex approval for Pro or dedicated-output tools.

Exit criteria: an approved synthetic helper can process a copied fixture and
produce independently verifiable output without changing the original.

Implementation and real Docker contract tests are complete. Exit acceptance is
pending one new dual-desktop-approved synthetic helper; the legacy smoke helper
has no standard entrypoint and remains deliberately non-runnable.

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
- [x] Retire active CNY 5 conversation budget tools in favor of the shared subscription ledger.
- [x] Add policy-routed repair modes and prompt-free lifecycle size metrics.
- [x] Require exact full-review attestations for Pro and output-producing tools.
- [x] Add on-demand workflow guidance and cross-desktop pending-review discovery.
- [x] Expose actual lifecycle API tokens separately from structural UTF-8 bytes.
- Run the fifteen-day trial and review quality, cost, latency, and failure data.
- Reassess subscription utilization pace and model routing after observing real workloads.

Exit criteria: both clients use the same policy and audit store, and trial data
supports a decision on production limits and model routing.

## Phase 7: provider-neutral external model workers and instruction cutover

Status: 0.13.0 test foundation in progress on an isolated branch. The profile,
worker-route and accounting boundaries are implemented without creating a new
accounting MCP process or changing the persistence schema. Deployment prompt
cutover and physical gray testing remain intentionally pending.

- Replace `DeepSeek` as a controller role name with the provider-neutral
  `external_model_worker`; retain provider names only inside provider adapters,
  model catalogs, credentials, pricing rules, and provider-specific audit data.
- Standardize public routing and audit fields around `provider`, `model_id`,
  `worker_role`, `capability_profile`, `account_alias`, `subscription_id`, and
  `pricing_catalog_version`. Do not introduce new `deepseek_*` public fields;
  preserve existing names only as documented compatibility aliases during a
  bounded migration window.
- Keep roles capability-bounded (`toolsmith`, `code_review`, and
  `source_structuring`) and continue to forbid a generic unrestricted prompt or
  direct working-tree mutation, regardless of provider or model.
- Keep entitlements and limits behind versioned provider policies. Active Core,
  Coding, and Review routes share the OpenCode Go rolling windows, per-model
  allowances, account isolation, and pricing ledger. Retain old DeepSeek CNY
  session rows only for historical audit compatibility.
- Make every executable a thin composition root driven by an explicit profile.
  Business capabilities (`toolsmith`, `coding`, `review`, `perception`, `h3`),
  provider routing, and accounting must remain separately testable modules.
- Split accounting responsibilities into entitlement checks, immutable usage
  ledger records, and user-approval policy. Expose only compact read-only status
  through MCP; do not create a required accounting MCP process.
- Add routing and compatibility tests proving that changing providers does not
  change controller authority, data minimization, isolation, review, approval,
  or audit guarantees.
- Treat global Codex guidance as a versioned local deployment artifact. Every
  release that changes providers, model roles, budget semantics, MCP tools, or
  approval flow must audit and, when required, update
  `%USERPROFILE%\.codex\AGENTS.md` after the matching runtime is deployed.
  Back up the prior local file, keep the persistent wording compact, and verify
  the new guidance from a fresh Codex session.
- At provider-neutral cutover, rename the global section from
  `Ask AI / DeepSeek` to `Ask AI / restricted external model worker`. Keep only
  cross-session authority, delegation, budget, data-safety, and workflow-routing
  rules in the global prompt; leave model- and protocol-specific details in MCP
  status/guidance responses and versioned project documentation.
- Make prompt maintenance an explicit deliverable of the first OpenCode-routed
  release, not an informal post-release cleanup. After the runtime and server
  contracts are deployed, remove the temporary DeepSeek-direct budget clause
  from the global guidance, audit the repository `AGENTS.md` for provider names
  used as role names, and deduplicate global host/worker rules from repository
  implementation rules.
- Record the global, repository, and combined instruction byte counts before and
  after cutover. Acceptance requires a smaller persistent instruction payload,
  no loss of authority or safety invariants, and a fresh-session check proving
  that Codex loaded the new provider-neutral guidance and MCP descriptions.

Exit criteria: the controller can switch a bounded worker between providers
without changing its public role contract or safety guarantees; no
active global instruction incorrectly treats `DeepSeek` as the role name; and a
fresh Codex session loads smaller guidance matching the deployed MCP version.

0.11.0 release gate: deploy and inspect the eight-tool Core surface, verify that
legacy CNY budget tools are absent, and confirm `usage_status` reports the USD 2
daily utilization pace as non-blocking. Then audit the user-level Codex
`AGENTS.md`; model catalogs and prices remain MCP status data.

0.12.0 release gate: centralize OpenCode Go generation policies; use a 131,072-token
total generation cap for thinking-heavy Core, Coding, and Review routes while
preserving the 4,096-token static-edit contract. Review preflight rejects only
inputs approaching the model context boundary, returns an advisory deterministic
partition plan with zero provider calls, and safely classifies request failures.
Collect 10-15 days of per-model reasoning/visible-output logs before tightening.

0.12.1 release gate: retain DeepSeek V4 Flash and GLM-5.3-Flash as the preferred
Coding routes; expose DeepSeek V4 Pro, GLM-5.3, and Kimi K3 only as explicit
advanced candidates for complex Coding work. All five routes use the existing
frozen commit, selected-file, repository-external candidate, strict-hash, shared
ledger, and no-automatic-retry boundary. Keep Phase 7 / 0.13 work frozen while
real 0.12.x Coding and Review evidence is collected.
