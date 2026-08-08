"""Connects to every downstream MCP server local-dc-agent's fulfillment loop
uses (see local-dc-agent/src/mcp_tools.py's McpToolRouter) and fetches each
tool's live inputSchema. Deliberately simpler than McpToolRouter: this is a
one-shot fetch for scoring, not a long-lived reconnecting router, so there's
no retry/backoff/reconnect logic to duplicate."""

from __future__ import annotations

from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

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
    async with AsyncExitStack() as stack:
        for label, base_url in _SERVERS.items():
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(f"{base_url}/mcp")
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            listed = await session.list_tools()
            for tool in listed.tools:
                schemas[f"{label}__{tool.name}"] = tool.inputSchema
    return schemas
