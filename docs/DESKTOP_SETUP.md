# Desktop setup

## Status and safety

Both desktop clients use the same local STDIO executable and the same
prompt-free SQLite audit store. Each client starts its own process, so seeing
two server processes is expected.

During phase 0, the server exposes only the read-only `usage_status` tool. Do
not add a DeepSeek API key yet. Credential Manager integration and the guarded
tool-building workflow arrive in phase 1 and phase 2.

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
      "args": []
    }
  }
}
```

Save the file, fully quit Claude Desktop, and reopen it. Closing only the window
is not sufficient. Confirm that `ask-ai` appears and that its only available
tool is `usage_status`.

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
enabled_tools = ["usage_status"]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 20
tool_timeout_sec = 60
```

Restart Codex Desktop after saving, then use `/mcp` to confirm the server and
tool catalog.

## Credential rule for later phases

The DeepSeek key will be written to Windows Credential Manager by a dedicated
setup command. It must not be placed in either desktop configuration file, a
repository file, `.env`, prompt, fixture, or ordinary log.

## Troubleshooting

- Run `uv sync --all-groups` after dependency or lock-file changes.
- Verify the executable exists at the absolute path above.
- Run `uv run fastmcp inspect src/ask_ai_mcp/server.py --format mcp` from the
  repository to inspect the advertised protocol surface.
- Restart the desktop client after configuration or server-code changes.
- MCP protocol data owns stdout; diagnostics must use stderr or the audit store.
