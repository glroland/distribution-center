"""Connects to every downstream MCP server local-dc-agent's fulfillment loop
uses (see local-dc-agent/src/mcp_tools.py's McpToolRouter) and fetches each
tool's live inputSchema. Deliberately simpler than McpToolRouter: this is a
one-shot fetch for scoring, not a long-lived reconnecting router, so there's
no retry/backoff logic to duplicate. Like McpToolRouter it holds no
session: each server is asked in one short-lived, self-contained exchange."""

from __future__ import annotations

from mcp import Client

from .settings import settings

_SERVERS = {
    "wms": settings.WMS_API_URL,
    "robot": settings.ROBOT_API_URL,
    "shipping": settings.SHIPPING_API_URL,
    "supervisor": settings.SUPERVISOR_API_URL,
    "label": settings.LABEL_API_URL,
}


async def fetch_live_tool_schemas() -> dict[str, dict]:
    """Returns {"<label>__<tool_name>": inputSchema} across all five servers --
    the real, currently-deployed contract, not a hardcoded guess at one."""
    schemas: dict[str, dict] = {}
    for label, base_url in _SERVERS.items():
        # Trailing slash: the servers mount their MCP app at /mcp, so /mcp
        # itself only answers with a redirect to /mcp/.
        async with Client(f"{base_url}/mcp/") as client:
            listed = await client.list_tools()
        for tool in listed.tools:
            schemas[f"{label}__{tool.name}"] = tool.input_schema
    return schemas
