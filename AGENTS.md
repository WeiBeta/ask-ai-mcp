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
- DeepSeek is a constrained toolsmith and source-structuring worker.
- Never add a generic `ask_deepseek(prompt)` or unrestricted delegation tool.
- Never use DeepSeek to draft final document prose, co-author delivery content,
  decide facts, resolve source conflicts, or generate final conclusions.
- DeepSeek output is untrusted until validated and reviewed by Opus/Sol.
- DeepSeek-generated code must be created outside the repository, tested in an
  isolated candidate workspace, and returned as a candidate patch. It must not
  modify the working tree directly.

## Source and artifact safety

- Original source files are read-only. Tools operate on copies and write to a
  dedicated output directory.
- File access must be restricted to configured allow-listed roots after path
  resolution. Reject traversal, symlinks escaping a root, and broad drive or
  user-profile access.
- Preserve source provenance: file hash, page/slide/sheet/cell location,
  extraction method, tool version, and warnings.
- Do not send an entire knowledge base to DeepSeek. Send only the minimum
  contract, sanitized fixtures, error excerpts, or selected evidence required.
- Do not log API keys, full prompts, full source text, or normal model outputs.

## Generated-tool lifecycle

1. Sol defines a bounded tool specification and acceptance tests.
2. DeepSeek Flash Thinking may generate a candidate.
3. Static checks and isolated tests run with bounded retries.
4. Sol reviews the code diff, test report, dependencies, and risk report.
5. Only an explicitly approved, hash-pinned tool may process real file copies.
6. Any code change returns the tool to unverified status.

Verified execution additionally requires the fixed `json_files_v1` contract,
an exact registered entrypoint, dedicated output, and both Claude Desktop and
Codex Desktop approval for Pro or output-producing tools. Never add arbitrary
commands, function names, output paths, container images, or mount options to
the MCP schema.

## Verification

- Add or update tests for every behavior change.
- Run `uv run pytest` and `uv run ruff check .` before committing.
- API integration tests must be opt-in and must never run during ordinary unit
  tests.
- Preserve unrelated user changes and keep commits focused.
