import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .settings import settings

logger = logging.getLogger(__name__)

_TOOL_NAME_SEPARATOR = "__"


class ToolCallError(Exception):
    """Raised when an MCP tool call reports an error result."""


@dataclass
class _Server:
    label: str
    session: ClientSession
    instructions: str | None


class McpToolRouter:
    """Connects to every fulfillment MCP server and exposes their tools as a
    single, name-prefixed OpenAI tool list, routing calls back to the right
    server. Connections are opened once and reused for the router's lifetime."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._servers: dict[str, _Server] = {}
        self._tools: list[dict] = []

    async def connect(self) -> None:
        server_urls = {
            "wms": settings.WMS_API_URL,
            "robot": settings.ROBOT_API_URL,
            "shipping": settings.SHIPPING_API_URL,
            "supervisor": settings.SUPERVISOR_API_URL,
        }
        for label, base_url in server_urls.items():
            logger.info("Connecting to MCP server %s at %s", label, base_url)
            read_stream, write_stream = await self._stack.enter_async_context(
                streamable_http_client(f"{base_url}/mcp")
            )
            session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
            init_result = await session.initialize()
            self._servers[label] = _Server(label=label, session=session, instructions=init_result.instructions)

            listed = await session.list_tools()
            for tool in listed.tools:
                self._tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"{label}{_TOOL_NAME_SEPARATOR}{tool.name}",
                            "description": tool.description or "",
                            "parameters": tool.input_schema,
                        },
                    }
                )
            logger.info("Connected to %s: %d tool(s) registered", label, len(listed.tools))

    async def close(self) -> None:
        await self._stack.aclose()

    def list_openai_tools(self) -> list[dict]:
        return list(self._tools)

    def server_instructions(self) -> dict[str, str]:
        return {label: server.instructions for label, server in self._servers.items() if server.instructions}

    async def call(self, name: str, arguments: dict) -> str:
        label, _, tool_name = name.partition(_TOOL_NAME_SEPARATOR)
        server = self._servers.get(label)
        if server is None:
            raise ToolCallError(f"Unknown tool server '{label}' for tool '{name}'")

        result = await server.session.call_tool(tool_name, arguments)
        text = "".join(part.text for part in result.content if hasattr(part, "text"))
        if result.is_error:
            logger.warning("MCP tool %s reported an error: %s", name, text)
            raise ToolCallError(text or f"Tool '{name}' failed")
        return text
