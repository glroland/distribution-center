import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass

import mlflow
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .settings import settings
from .tracing import configure_tracing

configure_tracing()

logger = logging.getLogger(__name__)

_TOOL_NAME_SEPARATOR = "__"
# A dependent MCP server (wms/robot/shipping/supervisor/label) is commonly
# still starting up when dc-agent's pod comes up during a fresh deploy or a
# cluster restart, since nothing enforces Kubernetes start-up ordering across
# services. Retrying with backoff here means that race resolves itself
# instead of leaving the agent permanently wedged.
_CONNECT_RETRY_INITIAL_DELAY_SECONDS = 1.0
_CONNECT_RETRY_MAX_DELAY_SECONDS = 30.0


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
        # Each server's transport/session context is kept in its own stack,
        # opened only once that server's connection attempt actually succeeds
        # (see _connect_with_retry) -- that way a failed attempt never leaks
        # a half-open connection into the router's lifetime.
        self._server_stacks: list[AsyncExitStack] = []
        self._servers: dict[str, _Server] = {}
        self._tools: list[dict] = []

    async def connect(self) -> None:
        server_urls = {
            "wms": settings.WMS_API_URL,
            "robot": settings.ROBOT_API_URL,
            "shipping": settings.SHIPPING_API_URL,
            "supervisor": settings.SUPERVISOR_API_URL,
            "label": settings.LABEL_API_URL,
        }
        for label, base_url in server_urls.items():
            session, instructions = await self._connect_with_retry(label, base_url)
            self._servers[label] = _Server(label=label, session=session, instructions=instructions)

            listed = await session.list_tools()
            for tool in listed.tools:
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
            logger.info("Connected to %s: %d tool(s) registered", label, len(listed.tools))

    async def _connect_with_retry(self, label: str, base_url: str) -> tuple[ClientSession, str | None]:
        """Connects to one MCP server, retrying with capped exponential backoff
        until it succeeds. Never gives up -- an unreachable server here is
        assumed to be a start-up ordering race (server not scheduled yet, still
        booting, etc.) rather than a permanent misconfiguration, so this blocks
        the caller rather than raising and killing the whole agent."""
        delay = _CONNECT_RETRY_INITIAL_DELAY_SECONDS
        attempt = 1
        while True:
            attempt_stack = AsyncExitStack()
            succeeded = False
            try:
                logger.info("Connecting to MCP server %s at %s (attempt %d)", label, base_url, attempt)
                read_stream, write_stream, _ = await attempt_stack.enter_async_context(
                    streamable_http_client(f"{base_url}/mcp")
                )
                session = await attempt_stack.enter_async_context(ClientSession(read_stream, write_stream))
                init_result = await session.initialize()
                succeeded = True
            except Exception as exc:  # noqa: BLE001 - any failure here means "not reachable yet", retry
                logger.warning(
                    "MCP server %s at %s not reachable yet (%s); retrying in %.1fs",
                    label, base_url, exc, delay,
                )
            finally:
                # Must close a failed attempt's contexts here, even when the
                # exception is a CancelledError (e.g. pod shutdown mid-retry)
                # that the `except Exception` clause above doesn't catch --
                # otherwise its anyio cancel scope is left open, which
                # corrupts cleanup ordering for every later close() call on
                # this task (surfacing as an unrelated-looking RuntimeError
                # far away, at shutdown).
                if not succeeded:
                    await attempt_stack.aclose()

            if succeeded:
                self._server_stacks.append(attempt_stack)
                return session, init_result.instructions

            await asyncio.sleep(delay)
            delay = min(delay * 2, _CONNECT_RETRY_MAX_DELAY_SECONDS)
            attempt += 1

    async def close(self) -> None:
        for stack in reversed(self._server_stacks):
            await stack.aclose()
        self._server_stacks.clear()

    def list_openai_tools(self) -> list[dict]:
        return list(self._tools)

    def server_instructions(self) -> dict[str, str]:
        return {label: server.instructions for label, server in self._servers.items() if server.instructions}

    async def call(self, name: str, arguments: dict) -> str:
        label, _, tool_name = name.partition(_TOOL_NAME_SEPARATOR)
        server = self._servers.get(label)
        if server is None:
            raise ToolCallError(f"Unknown tool server '{label}' for tool '{name}'")

        # Span name is set per-call (rather than via @mlflow.trace, whose name
        # is fixed at decoration time) so each MCP server's calls show up as
        # e.g. "wms__adjust_inventory" in the trace UI instead of every call
        # from every server appearing as the same generic "mcp_tool_call".
        with mlflow.start_span(name=f"mcp__{name}", span_type="TOOL") as span:
            span.set_inputs(arguments)
            result = await server.session.call_tool(tool_name, arguments)
            text = "".join(part.text for part in result.content if hasattr(part, "text"))
            if result.isError:
                logger.warning("MCP tool %s reported an error: %s", name, text)
                raise ToolCallError(text or f"Tool '{name}' failed")
            span.set_outputs(text)
            return text
