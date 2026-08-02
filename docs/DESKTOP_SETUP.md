# Desktop setup

## Status and safety

Both desktop clients use the same local STDIO executable and the same
prompt-free SQLite audit store. Each client starts its own process, so seeing
two server processes is expected.

The development server exposes usage status, four local budget-session tools,
candidate build/review/approval, registry listing, and verified execution.
`build_helper_tool` may incur DeepSeek charges. Budget operations, listing,
review, approval, and verified local execution make no external model call.
Real-file paths remain closed until narrow input roots are explicitly configured.

Use this absolute executable path:

```text
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp.exe
```

Absolute paths are required because a desktop MCP host may start a server with
an undefined working directory.

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
      "command": "C:\\Dev\\ask-ai-mcp\\.venv\\Scripts\\ask-ai-mcp.exe",
      "args": [],
      "env": {
        "ASK_AI_MCP_CLIENT_NAME": "claude_desktop"
      }
    }
  }
}
```

Save the file, fully quit Claude Desktop, and reopen it. Closing only the window
is not sufficient. Confirm that `ask-ai` advertises exactly ten tools:
`usage_status`, `open_budget_session`, `budget_status`, `add_budget_block`,
`close_budget_session`, `build_helper_tool`, `review_tool_candidate`,
`approve_tool_candidate`, `list_registered_tools`, and `run_verified_tool`.

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
command = "C:\\Dev\\ask-ai-mcp\\.venv\\Scripts\\ask-ai-mcp.exe"
cwd = "C:\\Dev\\ask-ai-mcp"
enabled = true
required = false
enabled_tools = ["usage_status", "open_budget_session", "budget_status", "add_budget_block", "close_budget_session", "build_helper_tool", "review_tool_candidate", "approve_tool_candidate", "list_registered_tools", "run_verified_tool"]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 20
tool_timeout_sec = 600

[mcp_servers.ask_ai.env]
ASK_AI_MCP_CLIENT_NAME = "codex_desktop"
PYTHONUTF8 = "1"
PYTHONIOENCODING = "utf-8"
```

Restart Codex Desktop after saving, then use `/mcp` to confirm the server and
ten-tool catalog. Keep approval mode set to `prompt` during the trial. Open one
opaque budget session per conversation that needs Ask AI. Flash starts with CNY
5, Pro with CNY 0, and either model is extended only in CNY 5 blocks after the
required confirmation. A normal build may make up to three billed API calls
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

When the opt-in API smoke test is approved, store the DeepSeek key through:

```powershell
uv run ask-ai-mcp-credentials set
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
