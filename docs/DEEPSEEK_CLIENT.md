# DeepSeek V4 client

## Implementation status

The internal client, mocked integration tests, Windows credential storage, and
real synthetic Flash smoke requests have passed. The direct client is retained
for historical audit compatibility and opt-in adapter tests. The active Core
protocol uses the configured subscription provider and Docker-readiness checks.

The 2026-08-01 smoke request used 656 cache-miss input tokens and 458 output
tokens, including 265 reasoning tokens. Estimated cost was CNY 0.001572. The
candidate remained in memory; no repository or job file was created.

## Official API contract

The client uses the OpenAI-compatible endpoint directly:

```text
POST https://api.deepseek.com/chat/completions
```

Supported routes are pinned to:

- `deepseek-v4-flash`: default tool-building route;
- `deepseek-v4-pro`: legacy direct route; not exposed by the active Core protocol.

Initial candidates and semantic-test repairs use thinking mode with
`reasoning_effort` set to `high` and a 131,072-token total generation cap. A single
static-policy repair disables thinking and uses a 4,096-token cap. Static
repair returns only a base-hash-bound list of exact unique text edits; semantic
repair still returns a complete candidate. Requests
also select JSON Output and explicitly instruct the model to return JSON
matching the server-provided candidate schema. There is no automatic model
upgrade from Flash to Pro.

Official references:

- <https://api-docs.deepseek.com/api/create-chat-completion>
- <https://api-docs.deepseek.com/guides/thinking_mode>
- <https://api-docs.deepseek.com/guides/json_mode/>

## Credential setup

The key is never accepted as a command-line argument because command arguments
can appear in shell history and process listings. From PowerShell 7 in the
repository, run:

```powershell
uv run ask-ai-mcp-credentials set
```

The command requests the key twice through a hidden prompt and stores it under
the `Ask AI MCP` service in Windows Credential Manager.

Safe status and deletion commands are:

```powershell
uv run ask-ai-mcp-credentials status
uv run ask-ai-mcp-credentials delete
```

The application rejects non-Windows or non-Windows-Credential-Manager keyring
backends. It does not read a DeepSeek key from environment variables, `.env`,
desktop MCP configuration, or repository files.

## Opt-in real smoke request

After storing the key, the only ordinary command that can perform the phase 1
smoke call requires an explicit charge confirmation:

```powershell
uv run ask-ai-mcp-smoke --confirm-charge
```

It always uses V4 Flash and a fixed synthetic delimiter-parser specification.
It sends no user documents, writes no candidate files, and prints only the
model, candidate SHA-256, and returned relative file names. Running the command
without `--confirm-charge` performs no API request.

## Request minimization

A tool-building request contains only:

- the bounded purpose;
- input and output contracts;
- acceptance tests;
- allow-listed packages;
- prohibited capabilities;
- minimal sanitized fixture notes.

It must not contain an entire knowledge base, original Office file, API key,
Git credential, or user-profile path.

## Response handling

The client accepts one schema-constrained JSON object containing a summary,
relative candidate files, and declared risks. It rejects:

- absolute, drive-qualified, backslash, or traversal paths;
- executable and unsupported file types;
- duplicate paths, including case-only duplicates;
- truncated, empty, malformed, oversized, or schema-invalid output.

Candidate content is hashed but remains untrusted. Chain-of-thought text is
discarded and is neither returned nor logged. HTTP errors expose only the
status code, never the response body.

## Audit behavior

For each external call, the SQLite usage store records model, client, status,
latency, cache-hit tokens, cache-miss tokens, output tokens, reasoning tokens,
estimated CNY cost, candidate hash, opaque budget/lifecycle IDs, and request and
response character counts. It does not record the API key, prompt, response
body, source text, candidate contents, or chain of thought. A separate lifecycle
audit stores only structural size metrics and outcomes.

## Test boundary

Ordinary tests use an in-memory HTTP transport and a disposable SQLite file.
They never require a real key and never access the network. A future smoke test
must be opt-in, produce only a harmless synthetic candidate, and consume the
credential through Windows Credential Manager.
