# Billed candidate lifecycle smoke

## Result

The first complete billed smoke passed on 2026-08-02 using
`deepseek-v4-flash` with thinking enabled. It used only a synthetic tool
specification and no source documents, knowledge-base content, credentials, or
business data.

- Tool: `normalize_synthetic_headers`
- Job ID: `1c33875c-19f8-4787-90fa-5693661916a2`
- Candidate SHA-256:
  `b9bbb3264118a8c8c58fafc887f509c6bbd7a79045be8d88b89389782a4e6222`
- Attempts: 1 initial build, 0 repairs
- Static findings: 0
- Isolated tests: 4 passed
- Runner: digest-pinned Python Linux image under Docker Desktop WSL 2
- Input-token increment: 717 cache-miss tokens
- Completion-token increment: 662 tokens
- Reasoning-token increment: 198 tokens
- Estimated cost increment: CNY 0.002041

The smoke command printed only the job ID, candidate hash, attempt count, and
test count. It did not print candidate source. The separate review operation
then reconstructed and revalidated the candidate patch for Sol inspection.

## Review and approval

The reviewed candidate was a pure `strip().casefold()` header-normalization
function with stdlib unittests covering the required example, input
immutability, duplicate ordering, and a new return-list object. It used no file,
network, environment, subprocess, or external-package capability.

After review, the exact candidate was registered as `0.0.1-smoke`, approved by
the server-configured `codex_desktop` identity. The only capability label is
`read_synthetic_inputs`. Registry reload and per-file hash verification passed.

This approval does not authorize real-file processing and the project still
exposes no `run_verified_tool` operation.
