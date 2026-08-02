# Candidate lifecycle and approval gate

## Scope

This lifecycle exists only for bounded helper-tool code. It does not authorize
DeepSeek to draft final document prose, select facts, resolve source conflicts,
or process real source files before promotion.

## Attempts and repair limit

The controller permits one initial DeepSeek candidate and at most two repair
rounds. Every replacement candidate receives a new content hash and restarts
the full sequence:

1. validate the structured response and candidate hash;
2. apply static AST and dependency policy;
3. stage a new job outside the repository;
4. run stdlib tests in the digest-pinned Docker sandbox;
5. either stop as failed, request a bounded repair, or return a review bundle.

Static failures send only finding codes and short messages. Runtime failures
send reason codes plus at most the final 8,000 characters of isolated test
stderr. Candidate-development jobs have empty or synthetic-only inputs, so
repair feedback must never contain knowledge-base or real source-document text.
There is no automatic escalation from Flash to Pro.

One user-approved `build_helper_tool` lifecycle can therefore create as many as
three billed DeepSeek API calls: the initial candidate and two bounded repair
calls. Usage accounting records each API call separately.

New candidates declare one simple Python entrypoint and the fixed
`json_files_v1` execution contract. Static analysis requires that entrypoint to
define exactly `run(request, input_dir, output_dir)`, and isolated candidate
testing verifies the callable exists. Every candidate must also contain at
least one `test_*.py` file with a discoverable stdlib `unittest.TestCase` and a
`test_*` method. `tempfile` is allowed only in test files for synthetic fixture
directories; it remains forbidden in production candidate code. Arbitrary CLI
commands and function names are not supported.

## Review bundle

A passing candidate remains `review_pending`. The bundle returned to Sol/Opus
contains:

- the exact candidate SHA-256 and source job ID;
- a unified patch for every candidate file;
- the test file list and bounded execution report;
- static-analysis findings and DeepSeek-declared risks;
- all attempt states, up to three total attempts.

The bundle is evidence for review, not an approval. It cannot process real file
copies and is not written into the repository.

## Explicit promotion

Promotion requires an explicit request containing the successful job ID, exact
candidate SHA-256, version, controller identity, decision, and allowed
capabilities. The registry then verifies:

- job ID, workspace directory, manifest, and execution report agree;
- isolated execution succeeded, did not time out, and ran at least one test;
- the candidate file set and every per-file SHA-256 are unchanged;
- the decision is a clean approval without untested modifications;
- the target tool name, version, and content hash are not already registered.

Approved bytes are copied to
`%LOCALAPPDATA%\AskAIMCP\registry\<tool>\<version>\<candidate-sha256>`.
Every later registry read re-hashes the copied files. Any edit invalidates the
record. “Approved with changes” is rejected: changes require a new candidate
hash and a fresh isolated test run.

An exact duplicate approval from the second desktop app adds its identity to
the immutable-code record. It does not create a new candidate or rerun
DeepSeek. Pro-built or dedicated-output tools require both desktop identities
before verified execution.

## Current exposure

MCP exposes separate build, review, approval, registry-list, and verified-run
tools. Build accepts only a strict `ToolBuildSpec`; review accepts only a UUID
job ID; approval accepts only the reviewed job ID, exact candidate SHA-256,
version, and capability labels. The server supplies the configured desktop
identity, so the caller cannot name its own approver. Verified execution accepts
only an exact registered identity, bounded JSON parameters, and allow-listed
source paths that are copied before Docker execution.
