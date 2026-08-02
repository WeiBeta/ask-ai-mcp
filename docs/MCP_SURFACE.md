# MCP candidate surface

The server deliberately has no arbitrary `ask_deepseek(prompt)` operation.
Desktop hosts receive four narrow tools.

## `usage_status`

Reads prompt-free aggregate usage from local SQLite. It makes no API call and
cannot read candidate or source contents.

## `build_helper_tool`

Accepts one strict `ToolBuildSpec` and an explicit Pro-approval flag. Before any
billable call it verifies that the Linux Docker runner and pinned image are
ready. It then permits one initial candidate and at most two repairs. The only
successful outcome is `review_pending`; the tool never promotes its own result.

The tool does not accept source paths, source files, arbitrary prompts, shell
commands, retry counts, image names, resource limits, or approval fields.

## `review_tool_candidate`

Accepts one UUID job ID. It performs no network or model call. Before returning
the patch and evidence it revalidates:

- job, manifest, execution report, static report, and review identities;
- candidate file set and per-file hashes;
- candidate payload hash, including summary and declared risks;
- deterministic patch equality.

Any changed byte or review field causes the read to fail.

## `approve_tool_candidate`

Accepts the reviewed job ID, exact candidate SHA-256, version, and capability
labels. The approver identity comes from `ASK_AI_MCP_CLIENT_NAME` in the server
process (`codex_desktop` or `claude_desktop`), never from model input.

Approval re-runs the review validation and the registry promotion checks. It
copies exact bytes into the registry but does not execute them. Modified code,
failed or missing tests, a different Docker image/backend, an external job
directory, or a mismatched hash is rejected.

## Deliberately absent

- generic DeepSeek chat or prompt forwarding;
- automatic Pro escalation;
- self-approval inside the build operation;
- execution against real source files;
- Office COM or PowerShell automation;
- network access from candidate containers;
- direct repository edits by generated code.
