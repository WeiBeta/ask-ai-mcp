"""Process entry point for the STDIO MCP server."""

from ask_ai_mcp.server import mcp


def main() -> None:
    """Run the MCP server without writing non-protocol data to stdout."""
    mcp.run()


if __name__ == "__main__":
    main()
