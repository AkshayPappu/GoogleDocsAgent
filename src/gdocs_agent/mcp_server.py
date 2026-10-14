"""Stdio MCP server exposing the Google Docs tools."""

from __future__ import annotations

from dotenv import load_dotenv

from gdocs_agent import docs_client
from gdocs_agent.tools import TOOL_FUNCTIONS


def main() -> None:
    load_dotenv()
    docs_client.INTERACTIVE = True
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("gdocs-agent")
    for function in TOOL_FUNCTIONS.values():
        server.tool()(function)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
