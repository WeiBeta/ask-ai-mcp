# Desktop setup

## Status and safety

Both desktop clients use the same local STDIO executable and the same
prompt-free SQLite audit store. Each client starts its own process, so seeing
two server processes is expected.

The development server exposes usage status plus separate candidate build,
review, and approval tools. It does not expose verified execution or real-file
processing. `build_helper_tool` may incur DeepSeek charges; approval only copies
an already-tested exact hash into the local registry.

Use this absolute executable path:

```text
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp.exe
```

Absolute paths are required because a desktop MCP host may start a server with
an undefined working directory.

## Claude Desktop on Windows

Open Claude Desktop, go to **Settings > Developer**, and select **Edit Config**.
The Windows configuration file is normally:

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
is not sufficient. Confirm that `ask-ai` advertises exactly four tools:
`usage_status`, `build_helper_tool`, `review_tool_candidate`, and
`approve_tool_candidate`.

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
enabled_tools = ["usage_status", "build_helper_tool", "review_tool_candidate", "approve_tool_candidate"]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 20
tool_timeout_sec = 600

[mcp_servers.ask_ai.env]
ASK_AI_MCP_CLIENT_NAME = "codex_desktop"
```

Restart Codex Desktop after saving, then use `/mcp` to confirm the server and
four-tool catalog. Keep approval mode set to `prompt` during the trial. A build
may make up to three billed API calls (one initial candidate and two repairs),
while review and approval make no external model call.

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
