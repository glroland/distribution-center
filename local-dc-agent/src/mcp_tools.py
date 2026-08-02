import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass

import mlflow
from fastmcp import Client

from .settings import settings
from .tracing import configure_tracing

configure_tracing()

logger = logging.getLogger(__name__)

_TOOL_NAME_SEPARATOR = "__"


class ToolCallError(Exception):
    """Raised when an MCP tool call reports an error result."""


@dataclass
class _Server:
    label: str
    client: Client
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
            client = await self._stack.enter_async_context(Client(f"{base_url}/mcp"))
            init_result = client.initialize_result
            instructions = init_result.instructions if init_result else None
            self._servers[label] = _Server(label=label, client=client, instructions=instructions)

            tools = await client.list_tools()
            for tool in tools:
                self._tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"{label}{_TOOL_NAME_SEPARATOR}{tool.name}",
                            "description": tool.description or "",
                            "parameters": tool.inputSchema,
                        },
                    }
                )
            logger.info("Connected to %s: %d tool(s) registered", label, len(tools))

    async def close(self) -> None:
        await self._stack.aclose()

    def list_openai_tools(self) -> list[dict]:
        return list(self._tools)

    def server_instructions(self) -> dict[str, str]:
        return {label: server.instructions for label, server in self._servers.items() if server.instructions}

    @mlflow.trace(span_type="TOOL", name="mcp_tool_call")
    async def call(self, name: str, arguments: dict) -> str:
        label, _, tool_name = name.partition(_TOOL_NAME_SEPARATOR)
        server = self._servers.get(label)
        if server is None:
            raise ToolCallError(f"Unknown tool server '{label}' for tool '{name}'")

        result = await server.client.call_tool(tool_name, arguments, raise_on_error=False)
        text = "".join(part.text for part in result.content if hasattr(part, "text"))
        if result.is_error:
            logger.warning("MCP tool %s reported an error: %s", name, text)
            raise ToolCallError(text or f"Tool '{name}' failed")
        return text
