# DeepSeek V4 client

## Implementation status

The internal client and its mocked integration tests are complete. It is not
yet exposed as an MCP tool because generated files must not leave memory or be
written to disk until the isolated candidate runner exists.

One real, harmless, explicitly approved smoke request remains before phase 1
meets its exit criteria. The command is implemented but will not run without a
charge-confirmation flag.

## Official API contract

The client uses the OpenAI-compatible endpoint directly:

```text
POST https://api.deepseek.com/chat/completions
```

Supported routes are pinned to:

- `deepseek-v4-flash`: default tool-building route;
- `deepseek-v4-pro`: requires an explicit `allow_pro` decision from Opus/Sol.

Tool candidates always use thinking mode with `reasoning_effort` set to
`high`. Requests also select JSON Output and explicitly instruct the model to
return JSON matching the server-provided candidate schema.

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
estimated CNY cost, and candidate hash. It does not record the API key, prompt,
response body, source text, candidate contents, or chain of thought.

## Test boundary

Ordinary tests use an in-memory HTTP transport and a disposable SQLite file.
They never require a real key and never access the network. A future smoke test
must be opt-in, produce only a harmless synthetic candidate, and consume the
credential through Windows Credential Manager.
