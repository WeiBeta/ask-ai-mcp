# Repository instructions

## Platform and tooling

- The current product target is Windows 11 only. Do not add macOS support in
  this development phase.
- Treat the Windows machine as a multipurpose physical host. System changes
  must be additive by default: do not disable, remove, or downgrade existing
  Windows features, Hyper-V, Containers, IIS, .NET Framework versions, or other
  shared runtimes without explicit approval for that exact change.
- Enforce project-specific restrictions inside the project runner or container
  configuration instead of reducing host capabilities.
- Use PowerShell 7 for scripts and automation.
- Use the system Python 3.13 installation and the system `uv` installation.
- Project dependencies belong in `.venv` and must be locked with `uv.lock`.
- Keep MCP stdout protocol-clean. Application logs go to stderr or the local
  audit store, never stdout.

## Model responsibility boundary

- Opus/Sol is the controller and final authority.
- Ask AI providers are constrained external-model workers. A provider or model
  name is routing metadata, not a role name.
- Never add a generic prompt-forwarding or unrestricted delegation tool.
- Never use an external worker to draft final document prose, co-author delivery content,
  decide facts, resolve source conflicts, or generate final conclusions.
- External-worker output is untrusted until validated and reviewed by Opus/Sol.
- Externally generated code must be created outside the repository, tested in an
  isolated candidate workspace, and returned as a candidate patch. It must not
  modify the working tree directly.
- Delegate only mechanically verifiable work likely to need at least three
  implementation rounds or to produce a reusable registered tool. The
  specification must be clearly shorter than the expected artifact.

## Source and artifact safety

- Original source files are read-only. Tools operate on copies and write to a
  dedicated output directory.
- File access must be restricted to configured allow-listed roots after path
  resolution. Reject traversal, symlinks escaping a root, and broad drive or
  user-profile access.
- Preserve source provenance: file hash, page/slide/sheet/cell location,
  extraction method, tool version, and warnings.
- Do not send an entire knowledge base to an external worker. Send only the minimum
  contract, sanitized fixtures, error excerpts, or selected evidence required.
- Do not log API keys, full prompts, full source text, or normal model outputs.

## Generated-tool lifecycle

1. Sol defines a bounded tool specification and acceptance tests.
2. The controller opens one opaque budget session for the current conversation.
3. The configured bounded toolsmith model may generate a candidate.
4. Static checks and isolated tests run with bounded policy-routed retries.
5. Sol reviews the summary, then loads the full patch when a code review is needed.
6. Only an explicitly approved, hash-pinned tool may process real file copies.
7. Any code change returns the tool to unverified status.

Flash receives CNY 5 automatically when a conversation budget session opens;
Pro starts at CNY 0. Either model may be extended only by one CNY 5 block after
explicit user confirmation. A started lifecycle may finish and slightly
overshoot, but the next lifecycle must be blocked until an extension is
confirmed. Lifecycle and API-call counts are audit metrics, not spending caps.
Never infer a Pro grant from the Flash budget or automatically escalate models.

Static-policy repair is limited to one attempt with Thinking disabled and a
4,096-token output cap. Semantic-test repair uses Thinking High with a
16,384-token cap, within the three-candidate-attempt lifecycle limit. An invalid
initial structured response may be regenerated once.

Verified execution additionally requires the fixed `json_files_v1` contract,
an exact registered entrypoint, dedicated output, and both Claude Desktop and
Codex Desktop approval for Pro or output-producing tools. Never add arbitrary
commands, function names, output paths, container images, or mount options to
the MCP schema.

Build and summary-review responses must remain compact. A full review records a
desktop-specific attestation bound to the exact job, candidate hash, and patch
hash. Pro or `write_dedicated_output` approval requires this attestation from
the approving desktop before promotion.

Keep persistent controller instructions and MCP tool descriptions concise. Use
`workflow_guidance` for detailed state-specific protocol and
`list_pending_reviews` for cross-desktop handoff instead of duplicating the
whole workflow in every prompt.

## Verification

- Add or update tests for every behavior change.
- Run `uv run pytest` and `uv run ruff check .` before committing.
- API integration tests must be opt-in and must never run during ordinary unit
  tests.
- Preserve unrelated user changes and keep commits focused.
