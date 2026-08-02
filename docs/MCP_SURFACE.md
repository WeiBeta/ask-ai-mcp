# MCP candidate surface

The server deliberately has no arbitrary `ask_deepseek(prompt)` operation.
Desktop hosts receive six narrow tools.

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

Calling approval for the same exact job, version, hash, and capabilities from
the other desktop app appends the second approval identity without changing
candidate bytes. Output-producing and Pro-built tools are not runnable until
both `claude_desktop` and `codex_desktop` have approved them.

## `list_registered_tools`

Performs a prompt-free local registry read and rehashes every registered file.
It returns version, exact hash, capabilities, approval identities, execution
count, whether the tool is runnable, and any blocking reasons. Legacy smoke
registrations remain visible but are not made runnable by guessing an entrypoint.

## `run_verified_tool`

Accepts only a registered name, version, exact candidate hash, a bounded JSON
parameter object, and up to 50 absolute input-file paths. It never accepts a
command, module name, function name, image, network option, or output path.

The tool requires the fixed `json_files_v1` contract. A trusted harness calls
the registered Python `run(request, input_dir, output_dir)` function inside the
digest-pinned Docker image. Candidate and staged inputs are read-only; only a
private per-run output directory is writable. Networking, capabilities,
privilege escalation, host credentials, repository access, and the Docker
socket remain unavailable.

Source paths must resolve under one of the semicolon-separated roots in
`ASK_AI_MCP_ALLOWED_INPUT_ROOTS`. Drive roots and the entire user profile are
rejected. The variable is absent by default, so real-file execution is closed
until the user chooses a narrow business-input directory. Inputs are copied and
hashed before execution; original files are never mounted. The response returns
artifact metadata and hashes, not file contents.

## Deliberately absent

- generic DeepSeek chat or prompt forwarding;
- automatic Pro escalation;
- self-approval inside the build operation;
- mounting or modifying original source files;
- Office COM or PowerShell automation;
- network access from candidate containers;
- direct repository edits by generated code.
