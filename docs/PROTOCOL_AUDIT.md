# MCP protocol audit

Ask AI MCP records prompt-free protocol metadata in the existing local SQLite
audit database. The purpose is to measure tool discovery frequency, tool-surface
size, tool selection, and server-side latency before changing the MCP routing
architecture.

## Scope

The FastMCP middleware observes three request types:

- `initialize`: configured and reported client identity, client version, and MCP
  protocol version.
- `tools/list`: tool count, canonical tool-surface byte count, SHA-256, duration,
  and outcome.
- `tools/call`: tool name, argument and result byte counts, duration, and outcome.

The byte counts are canonical UTF-8 JSON measurements. `tool_surface_bytes`
measures the returned tool definitions without the surrounding JSON-RPC envelope,
so it is a stable comparison metric rather than an exact network-packet size.

The audit never stores prompts, argument values, tool-definition bodies, result
content, exception messages, API keys, or source-file content. Error values are
reduced to a small category such as `timeout`, `cancelled`, `validation`,
`backend_unavailable`, or `internal`.

## Storage and behavior

Events are appended to `mcp_protocol_events` in the same `usage.db` selected by
`ask_ai_mcp.usage.default_usage_db_path()`. SQLite WAL mode and the existing busy
timeout allow the Codex Desktop and Claude Desktop MCP processes to share the
database.

Auditing is enabled by default. Set `ASK_AI_MCP_PROTOCOL_AUDIT=0` before starting
the MCP process to disable it. Values `false`, `no`, and `off` are also accepted.

Audit writes are fail-open: a database failure does not change the underlying MCP
response. The server emits at most one compact warning per process and never
writes application diagnostics to MCP stdout.

Protocol events currently have no automatic retention policy. Each row contains
only bounded structural metadata; retention can be added after real growth is
measured.

## Interpretation limits

A `tools/list` event proves that the MCP client requested the tool definitions. It
does not prove that the client placed every returned schema into the model context.
Likewise, the tool-surface byte count is a maximum-payload comparison metric, not a
token count.

Use these records together with controlled MCP-on/MCP-off or schema-amplification
A/B tests. For routing decisions, compare request frequency, incorrect tool calls,
server latency, client-reported input/cache tokens when available, and task success.
