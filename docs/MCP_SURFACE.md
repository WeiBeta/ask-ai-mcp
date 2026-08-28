# MCP candidate surface

The server deliberately has no arbitrary model-prompt forwarding operation.
Core advertises eight tools, Subagent advertises eleven, and compatibility
Full advertises fifteen. Perception-only advertises exactly three source tools;
H3-only advertises exactly four local video tools.
Review-only advertises exactly four bounded review tools and is absent from
all five existing surfaces, including compatibility Full.
Coding-only advertises exactly three coding-candidate tools and is also absent
from Core, Subagent, Perception, H3, Review, and compatibility Full.

In the 0.13 test foundation these executable names are compatibility profiles,
not provider or accounting boundaries. Profiles compose explicit capabilities;
provider/model routing and the shared entitlement/ledger/approval services are
separately tested internal modules. There is no accounting MCP process and no
second usage database. Tool order and input parameter schemas remain frozen to
the 0.12.3 surface during initial gray testing.

## Optional bounded coding worker

`ask-ai-mcp-coding.exe` exposes `coding_backend_status`, `coding_submit`, and
paginated `coding_status`. It accepts one allow-listed repository ID, an exact
commit, named target/context files, a bounded task contract, and only
five fixed models. `deepseek-v4-flash` and `glm-5.3-flash` are the preferred
routes; `deepseek-v4-pro`, `glm-5.3`, and `kimi-k3` are explicit advanced
candidates for complex work. Every route requests `max` reasoning and a
131,072-token total generation cap exposed by backend status and job metadata.
Advanced routes are never selected as an automatic fallback.
It freezes source with read-only Git operations and emits an external candidate
diff; it has no generic prompt, arbitrary path/model/URL, shell, test runner,
working-tree write, apply, commit, push, or automatic account-switch operation.

## Optional heterogeneous code review

`ask-ai-mcp-review.exe` is manually enabled only for heavy development review.
It exposes `code_review_backend_status`, `code_review_stage_patch`,
`code_review_submit`, and the paginated `code_review_status`. Stage validates
and atomically seals a patch plus receipt in the repository's dedicated external
root. Submit accepts an allow-listed repository ID plus either two bounded refs
or both staged hashes, one fixed profile, and one of three fixed
OpenCode Go models. It has no generic prompt, shell, arbitrary path, Git-write,
patch-generation, commit, push, or retry option.

Review models are fixed to `deepseek-v4-pro`, `glm-5.3`, and `kimi-k3`, each
with requested reasoning effort `max` and a 131,072-token total generation cap.
Before a paid request, a context-boundary preflight either permits the single
call or returns `REVIEW_PARTITION_REQUIRED` plus an advisory deterministic plan;
it never automatically partitions, submits, or retries.

The controller resolves immutable commits, removes secrets, binary/vendor/generated
content, submodules, escaping reparse points, oversized files, and excess context,
then sends only repository-relative diff fragments. Findings are strict JSON and
must point to a supplied changed hunk. Full inputs and outputs remain in separated
per-job artifacts; the SQLite review ledger stores hashes, metrics, adjudications,
and outcomes but not full source, diff, prompt, credential, or ordinary model output.
Status keeps model names hidden while a person or Sol records adjudication.

## Multimodal source tools

Perception-only exposes exactly three asynchronous source tools. Subagent and
Full include the same three tools for compatibility:

- `source_backend_status` reports local backend identity, readiness, fixed
  extraction profiles, one-job GPU concurrency, and input-root configuration.
- `source_extract` accepts allow-listed absolute file paths and one of
  `document_evidence` or `visual_structure`. It has no arbitrary prompt field.
  Visual extraction defaults to `structure_index`, which returns one bounded
  title/region/decision/interface index per visual. `topology` separately
  returns nodes and connectors. A `selected_details` request accepts at most
  eight bounded `focus_ids` plus one normalized `[x, y, width, height]`
  `focus_region_xywh`, crops that region before inference, and returns exactly
  one detail record for each requested identifier. This scope accepts exactly
  one visual image, rendered page, or rendered slide per request.
- `source_job_status` returns progress and hash-addressed output artifacts.

Inputs are copied into a private job directory and hashed before and after the
copy. Originals are never mounted or modified. Backend output must validate as
`canonical_evidence_v1`, preserve source hashes and locations, and contain
source-faithful text or structured data rather than final conclusions. With
`ASK_AI_MCP_SOURCE_PROVIDER=local_qwen`, PDF pages are rendered by locked
PDFium and PPTX slides by a fixed, read-only PowerPoint COM exporter after macro,
external-relationship, and package-safety checks. Document jobs are capped at
32 selected visuals and use one visual per Qwen request because the current
runtime did not preserve every image in a multi-image request. With
`ASK_AI_MCP_SOURCE_PROVIDER=opencode`, the same staging, rendering, provenance,
validation, and output contract uses Qwen3.8 Max through the fixed OpenCode Go
Anthropic Messages endpoint. The real backend remains unavailable unless its
selected credential/runtime and narrow input roots are configured.
The 0.7.1 source surface deliberately does not advertise DOCX, XLSX, video,
audio, media-timeline, or time-range inputs; those require separate validated
preprocessors before they can return to the public schema.

The visual index deliberately forbids interface caller, request-field,
response-field, and explanatory-paragraph expansion. Those details require a
separate `selected_details` request with a deterministic crop. The backend
validates the exact bounded shape and returned crop coordinates after model
output, so increasing model output limits cannot silently turn one overview
request into an unbounded transcription.

## Local MiniMax H3 video tools

All four H3 tools first check the loopback ComfyUI server. If it is unavailable,
they run the absolute `.ps1` configured by `ASK_AI_MCP_H3_START_SCRIPT` once and
wait up to `ASK_AI_MCP_H3_START_TIMEOUT_SECONDS` for readiness. Concurrent MCP
processes are serialized by the launcher's named mutex. `h3_backend_status`
therefore has a state-changing annotation even though its returned information
is diagnostic. It checks the exact four-file
FL2VA model set, and five explicit post-processing weights. `h3_generate_video`
submits only a bounded 4–15 second 480p/24 fps original. Five to fifteen seconds
is recommended because it maps to the model's approximate 124–362 trained frame
range after `17k+5` alignment; four seconds is valid but aligns to 107 frames.
After a person selects
a usable original, `h3_postprocess_video` applies an explicit anime or realistic
interpolation/upscale combination without asking ComfyUI to infer the style.
`h3_job_status` polls either asynchronous job and returns validated local output
paths and loopback view URLs. When a job reaches a terminal state and the global
ComfyUI queue is idle, it calls the loopback `/free` endpoint with both
`unload_models` and `free_memory` enabled. This keeps ComfyUI running while
unloading models and releasing GPU memory. Repeated terminal polling does not
repeat a successful release request in the same MCP process.

The adapter rejects non-loopback backend URLs, arbitrary output paths, and
reference images outside `ASK_AI_MCP_H3_INPUT_ROOTS`. Reference images go in
`C:\Users\user\Documents\AskAI-Exchange\H3-Workspace\inputs`; generated media
goes in the sibling `outputs` directory. This shared workspace is the only H3
task-data root granted to both desktop clients. The runtime and weights remain
under `C:\AI\ComfyUI`, outside this repository. Use is subject
to the MiniMax H3 Community License Agreement, including its territory and
acceptable-use terms.

## `usage_status`

Reads prompt-free aggregate usage from local SQLite. It makes no API call and
cannot read candidate or source contents. It includes at most twenty recent
lifecycle-economics rows. API cache-hit, cache-miss, completion, and reasoning
token counts are provider usage. Specification, candidate, test, summary, and
patch sizes are separately labelled character or UTF-8 byte measurements, with
byte ratios; they are not presented as tokens. OpenCode calls also report
virtual USD by provider model/account and conservative rolling 5-hour, 7-day,
and 30-day allowance windows. It also reports current UTC-day spend against a
USD 2 utilization pace. That pace is informational and never blocks a call.

## `workflow_guidance`

Loads one local protocol topic on demand: `overview`, `usage`, `build`,
`review`, `approval`, or `run`. It is read-only, prompt-free, and makes no API
call. This avoids repeating detailed protocol in every persistent prompt or
tool description.

## `list_pending_reviews`

Returns a compact local cross-desktop queue without patches or source content.
It includes unregistered review-pending candidates and registered Pro or
output-writing tools still waiting for the second desktop. Each item reports
creator, model, hashes, registered versions, exact full-review attestation
identities, approval identities, blocking reasons, and the next action.

## `build_helper_tool`

Accepts one strict `ToolBuildSpec`. Before a billable call it verifies that the
Linux Docker runner and pinned image are ready. Provider selection is separate
from MCP profiles, and the configured provider enforces shared subscription
windows and model allowances. It then permits one initial candidate and at most
two repairs. The only successful outcome is `review_pending`; the tool never
promotes its own result. Direct DeepSeek CNY budget sessions are retained only
as historical audit data and are not part of the active MCP surface.

The tool does not accept source paths, source files, arbitrary prompts, shell
commands, retry counts, image names, resource limits, Pro overrides, or
approval fields.

## `review_tool_candidate`

Accepts one UUID job ID and `summary` or `full` mode. It performs no network or
model call. Summary mode is the default and returns hashes, sizes, file lists,
findings, tests, risks, and attempt summaries without returning the candidate
patch. It also computes creator, attestation identities, approval identities,
blocking reasons, and next action from local state. Full mode returns the exact patch and records a desktop-specific review
attestation. Before either response it revalidates:

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

Candidates requesting `write_dedicated_output`, and all Pro candidates, require
a current full-review attestation from the approving desktop for the exact job,
candidate hash, and patch hash. Summary inspection alone cannot satisfy this
gate.

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

## Replay capture

`ASK_AI_MCP_REPLAY_CAPTURE=1` enables content-bearing toolsmith replay capsules
in a separate local replay root. Capsules contain sanitized specifications,
every parsed candidate, bounded repair feedback, static findings, execution
results, prompt-template identity, and terminal outcome. Each capsule has an
independent SHA-256 file. They are never written into the prompt-free usage or
MCP protocol audit tables. Storage failure is fail-open for live work.
