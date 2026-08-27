# Desktop setup

For the current workstation's Simplified Chinese operating procedure, standard
handoff directory, backup boundaries, and migration checklist, see
`USER_MANUAL_ZH-CN.md`.

## Status and safety

Both desktop clients use the same local STDIO executable and the same
prompt-free SQLite audit store. Each client starts its own process, so seeing
two server processes is expected.

The development server exposes four local H3 video tools, usage and lifecycle
economics, on-demand workflow
guidance, cross-desktop review discovery, four local budget-session tools,
candidate build/review/approval, registry listing, and verified execution.
`build_helper_tool` may incur DeepSeek charges. Budget operations, listing,
review, approval, and verified local execution make no external model call.
Real-file paths remain closed until narrow input roots are explicitly configured.

Keep Core always on with this absolute executable path:

```text
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp-core.exe
```

Register additional explicit profiles only where needed:

```text
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp-core.exe  # 12 non-video tools
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp-subagent.exe  # Core + 3 Qwen source tools
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp-h3.exe  # 4 H3 tools only
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp-coding.exe  # 3 coding candidate tools, opt-in
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp-review.exe  # 4 review tools, opt-in
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp-full.exe  # compatibility union, 19 tools
```

Core does not register source or H3 tools. Subagent and H3 can be registered under
separate server names and left disabled in clients that expose MCP switches. Full
is retained for compatibility, not recommended as the default. All external model
runtimes, workspaces, and media remain outside this repository.

Absolute paths are required because a desktop MCP host may start a server with
an undefined working directory.

The ComfyUI/H3 runtime is installed outside the repository at
`C:\AI\ComfyUI`. Full automatically starts it through the bounded PowerShell
launcher when the loopback service is unavailable. The same launcher can be run
manually:

```powershell
pwsh -NoProfile -File C:\Dev\ask-ai-mcp\scripts\start_comfyui_h3.ps1
```

The server listens only on `127.0.0.1:8188`. Put optional first/last-frame
images in `C:\Users\user\Documents\AskAI-Exchange\H3-Workspace\inputs`;
generated media is written to the sibling `outputs` directory.

## Claude Desktop on Windows

Open Claude Desktop, go to **Settings > Developer**, and select **Edit Config**.
For the Microsoft Store/MSIX installation used on this workstation, **Edit
Config** opens the active file at:

```text
C:\Users\user\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json
```

Other Claude Desktop distributions may instead use:

```text
%APPDATA%\Claude\claude_desktop_config.json
```

Merge this server into the existing `mcpServers` object. Do not overwrite other
configured servers:

```json
{
  "mcpServers": {
    "ask-ai": {
      "command": "C:\\Dev\\ask-ai-mcp\\.venv\\Scripts\\ask-ai-mcp-core.exe",
      "args": [],
      "env": {
        "ASK_AI_MCP_CLIENT_NAME": "claude_desktop",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

Save the file, fully quit Claude Desktop, and reopen it. Closing only the window
is not sufficient. Confirm that `ask-ai` advertises exactly twelve Core tools.
Use the profile templates under `migration/profiles` when Claude needs Subagent,
H3, Review, or compatibility Full. The Review JSON is an offline registration
example: do not merge it for ordinary work. Import and enable it from Claude's
Extensions controls only for an explicit review session, then disable it again.

For later private packaging, Claude Desktop also supports a local desktop
extension bundle. We will evaluate that only after the server workflow is
stable; raw local configuration is easier to inspect during development.

## Codex Desktop on Windows

Codex Desktop, Codex CLI, and the IDE extension share the same Codex MCP
configuration. The default user configuration is:

```text
%USERPROFILE%\.codex\config.toml
```

The desktop UI can add the server through **Settings > MCP servers > Add
server**. For an explicit configuration, merge the following table into the
existing TOML file:

```toml
[mcp_servers.ask_ai]
command = "C:\\Dev\\ask-ai-mcp\\.venv\\Scripts\\ask-ai-mcp-core.exe"
cwd = "C:\\Dev\\ask-ai-mcp"
enabled = true
required = false
enabled_tools = ["usage_status", "workflow_guidance", "list_pending_reviews", "build_helper_tool", "review_tool_candidate", "approve_tool_candidate", "list_registered_tools", "run_verified_tool"]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 20
tool_timeout_sec = 600

[mcp_servers.ask_ai.env]
ASK_AI_MCP_CLIENT_NAME = "codex_desktop"

[mcp_servers.ask_ai_h3]
command = "C:\\Dev\\ask-ai-mcp\\.venv\\Scripts\\ask-ai-mcp-h3.exe"
cwd = "C:\\Dev\\ask-ai-mcp"
enabled = false
required = false
enabled_tools = ["h3_backend_status", "h3_generate_video", "h3_postprocess_video", "h3_job_status"]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 20
tool_timeout_sec = 600

[mcp_servers.ask_ai_h3.env]
ASK_AI_MCP_CLIENT_NAME = "codex_desktop"
ASK_AI_MCP_H3_URL = "http://127.0.0.1:8188"
ASK_AI_MCP_H3_START_SCRIPT = "C:\\Dev\\ask-ai-mcp\\scripts\\start_comfyui_h3.ps1"
ASK_AI_MCP_H3_START_TIMEOUT_SECONDS = "120"
ASK_AI_MCP_H3_WORKSPACE_ROOT = "C:\\Users\\user\\Documents\\AskAI-Exchange\\H3-Workspace"
ASK_AI_MCP_H3_COMFY_INPUT_ROOT = "C:\\Users\\user\\Documents\\AskAI-Exchange\\H3-Workspace\\inputs"
ASK_AI_MCP_H3_OUTPUT_ROOT = "C:\\Users\\user\\Documents\\AskAI-Exchange\\H3-Workspace\\outputs"
ASK_AI_MCP_H3_INPUT_ROOTS = "C:\\Users\\user\\Documents\\AskAI-Exchange\\H3-Workspace\\inputs"
PYTHONUTF8 = "1"
PYTHONIOENCODING = "utf-8"

[mcp_servers.ask_ai_review]
command = "C:\\Dev\\ask-ai-mcp\\.venv\\Scripts\\ask-ai-mcp-review.exe"
cwd = "C:\\Dev\\ask-ai-mcp"
enabled = false
required = false
enabled_tools = ["code_review_backend_status", "code_review_stage_patch", "code_review_submit", "code_review_status"]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 20
tool_timeout_sec = 900

[mcp_servers.ask_ai_review.env]
ASK_AI_MCP_CLIENT_NAME = "codex_desktop"
ASK_AI_MCP_REVIEW_REPOSITORIES = '{"ask-ai-mcp":"C:\\Dev\\ask-ai-mcp"}'
ASK_AI_MCP_REVIEW_PATCH_ROOTS = ""
ASK_AI_MCP_OPENCODE_ACCOUNT_UID = "<API-visible-UID>"
ASK_AI_MCP_OPENCODE_ACCOUNT_ALIAS = "<Go-account-name>"
PYTHONUTF8 = "1"
PYTHONIOENCODING = "utf-8"

[mcp_servers.ask_ai_coding]
command = "C:\\Dev\\ask-ai-mcp\\.venv\\Scripts\\ask-ai-mcp-coding.exe"
cwd = "C:\\Dev\\ask-ai-mcp"
enabled = false
required = false
enabled_tools = ["coding_backend_status", "coding_submit", "coding_status"]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 20
tool_timeout_sec = 900

[mcp_servers.ask_ai_coding.env]
ASK_AI_MCP_CLIENT_NAME = "codex_desktop"
ASK_AI_MCP_CODING_REPOSITORIES = '{"ask-ai-mcp":"C:\\Dev\\ask-ai-mcp"}'
ASK_AI_MCP_OPENCODE_ACCOUNT_UID = "<API-visible-UID>"
ASK_AI_MCP_OPENCODE_ACCOUNT_ALIAS = "<Go-account-name>"
PYTHONUTF8 = "1"
PYTHONIOENCODING = "utf-8"
```

Restart Codex Desktop after saving, then use the settings UI to confirm Core has
eight tools and H3 is registered but disabled. Keep approval mode set to `prompt`.
Coding and Review must remain disabled until their specialized session. One paid
Go account maps to one API-visible UID and one Credential Manager key; the optional
alias is only a display name. Both modules share the UID-keyed ledger.
Use `usage_status` to inspect the shared subscription ledger. USD 2/day is an
informational utilization pace, not a hard daily cap. A normal build may make up to three billed API calls
(one initial candidate and two repairs); one invalid initial structured response
may add a single regeneration call.

Do not add `ASK_AI_MCP_ALLOWED_INPUT_ROOTS` until a narrow source-copy directory
has been chosen. When enabled later, use a semicolon-separated list of explicit
subdirectories. Drive roots and the complete user profile are intentionally
rejected. The same variable must be set in both desktop server environments if
both clients will run verified tools.

The UTF-8 environment settings are required on this workstation. Without them,
Codex App Server can reject otherwise valid MCP startup because Python stderr
contains bytes that are not valid UTF-8.

## Controller instructions

MCP configuration connects the executable but does not by itself define when a
host model should delegate work. Apply the shared and client-specific
instructions in `docs/CONTROLLER_PROMPTS.md`.

Codex loads its adaptation from the user-level `%USERPROFILE%\.codex\AGENTS.md`
on this workstation. Claude personal preferences or project instructions are
managed by Claude's UI/account state and must not be injected into
`claude_desktop_config.json` or an undocumented application database. Paste the
shared policy and the Claude adaptation through the Claude UI, then start a new
conversation so the instructions are in context.

## Credential rule

When the opt-in OpenCode Go smoke test is approved, store the key through:

```powershell
uv run ask-ai-mcp-credentials set --provider opencode-go --account-uid <API-visible-UID>
```

The hidden interactive prompt writes it to Windows Credential Manager. It must
not be placed in either desktop configuration file, a repository file, `.env`,
prompt, fixture, or ordinary log.

## Troubleshooting

- Run `uv sync --all-groups` after dependency or lock-file changes.
- Verify the executable exists at the absolute path above.
- Run `uv run fastmcp inspect src/ask_ai_mcp/server.py --format mcp` from the
  repository to inspect the advertised protocol surface.
- Restart the desktop client after configuration or server-code changes.
- MCP protocol data owns stdout; diagnostics must use stderr or the audit store.
