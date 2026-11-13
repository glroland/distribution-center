import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio
import mlflow
from mcp import Client, MCPError
from mcp.types import DiscoverResult

from . import guardrails
from .settings import settings
from .tracing import configure_tracing

configure_tracing()

logger = logging.getLogger(__name__)

_TOOL_NAME_SEPARATOR = "__"
# Demo/test-reset tools exist on their servers for `make reset`-style demo
# resets, not as something the fulfillment LLM should ever be able to
# invoke on its own initiative -- there's no legitimate reason a purchase
# order would need to wipe the WMS ledger, return the robot to the dock
# empty-handed, or clear shipment history mid-fulfillment. Hidden from
# list_openai_tools() and refused by call() while guardrails.is_enabled()
# (the "Agentic Safety" toggle) is on; still discovered and kept in
# self._tools either way so flipping the toggle off doesn't require
# rediscovering them.
_DISALLOWED_TOOLS = {"wms__reset_inventory", "robot__reset_robot", "shipping__reset_shipments"}
# A dependent MCP server (wms/robot/shipping/supervisor/label) is commonly
# still starting up when dc-agent's pod comes up during a fresh deploy or a
# cluster restart, since nothing enforces Kubernetes start-up ordering across
# services. Retrying discovery with backoff here means that race resolves
# itself instead of leaving the agent permanently wedged.
_DISCOVER_RETRY_INITIAL_DELAY_SECONDS = 1.0
_DISCOVER_RETRY_MAX_DELAY_SECONDS = 30.0


class ToolCallError(Exception):
    """Raised when an MCP tool call reports an error result."""


class _ServerUnreachableError(Exception):
    """One request to an MCP server failed at the transport level (refused,
    reset, non-MCP response) rather than being answered by the server."""


@dataclass
class _Server:
    """What discovery learned about one MCP server -- everything a later
    request needs to stand entirely on its own, with nothing held open."""

    label: str
    url: str
    instructions: str | None
    # Replayed into every later request's Client (mode=/prior_discover=) so it
    # goes straight to the server as a single POST instead of re-running
    # server/discover first.
    protocol_version: str
    discover: DiscoverResult


class McpToolRouter:
    """Discovers every fulfillment MCP server's tools and exposes them as a
    single, name-prefixed OpenAI tool list, routing calls back to the right
    server.

    Entirely stateless on the wire: the downstream servers speak the
    session-less MCP protocol revision (2026-07-28 -- no initialize handshake,
    no Mcp-Session-Id), and this router holds no connection to any of them
    between requests. Discovery (`server/discover` + `tools/list`) and every
    tool call are each one self-contained HTTP exchange through a short-lived
    Client. That is what makes a downstream pod restart a non-event: there is
    no session for it to strand and no long-lived stream for it to sever, so
    the next call simply reaches whichever instance is up. (The persistent
    sessions this replaced needed dedicated reconnect logic for exactly that
    case, and a manual dc-agent restart before that logic existed.)"""

    def __init__(self) -> None:
        self._servers: dict[str, _Server] = {}
        self._tools: list[dict] = []

    async def connect(self) -> None:
        """Discovers all five servers. "Connect" in name only -- nothing stays
        open afterwards; this blocks until each server has answered once."""
        server_urls = {
            "wms": settings.WMS_API_URL,
            "robot": settings.ROBOT_API_URL,
            "shipping": settings.SHIPPING_API_URL,
            "supervisor": settings.SUPERVISOR_API_URL,
            "label": settings.LABEL_API_URL,
        }
        for label, base_url in server_urls.items():
            await self._register(label, base_url)

    async def _register(self, label: str, base_url: str) -> None:
        """Discovers one server and (re)registers its tools. Safe to call
        again for a label that's already registered -- any stale `label__*`
        entries in the tool list are replaced rather than duplicated."""
        server, listed_tools = await self._discover_with_retry(label, base_url)
        self._servers[label] = server

        prefix = f"{label}{_TOOL_NAME_SEPARATOR}"
        self._tools = [tool for tool in self._tools if not tool["function"]["name"].startswith(prefix)]
        for tool in listed_tools:
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
        disallowed_count = sum(1 for tool in listed_tools if f"{label}{_TOOL_NAME_SEPARATOR}{tool.name}" in _DISALLOWED_TOOLS)
        logger.info(
            "Discovered %s: %d tool(s) registered (%d hidden while Agentic Safety is on)",
            label, len(listed_tools), disallowed_count,
        )

    async def _discover_with_retry(self, label: str, base_url: str) -> tuple[_Server, list]:
        """Fetches one MCP server's instructions and tool list, retrying with
        capped exponential backoff until it succeeds. Never gives up -- an
        unreachable server here is assumed to be a start-up ordering race
        (server not scheduled yet, still booting, etc.) rather than a
        permanent misconfiguration, so this blocks the caller rather than
        raising and killing the whole agent.

        Each attempt is bounded by MCP_CONNECT_TIMEOUT_SECONDS: a server whose
        process is up and accepting TCP connections but hasn't actually
        started reading from the socket yet (e.g. label-api/wms still loading
        data/model files before uvicorn's event loop is running) leaves the
        connection sitting in the kernel's accept queue -- our request goes
        out, but nothing ever reads or responds to it. Without a bound that
        wedges the attempt forever, since the retry below only runs on an
        exception, never on a hang. See _exchange for how the bound is
        applied and which failure shapes it normalizes.

        A server that only speaks a handshake-era (pre-2026-07-28, session
        based) protocol revision is treated the same as one that isn't up
        yet: this router never opens a session, so it can't use that server
        until it's upgraded."""
        # Trailing slash: the servers mount their MCP app at /mcp, so the
        # endpoint itself is /mcp/ -- asking for /mcp costs a 307 round trip
        # on every single request.
        url = f"{base_url}/mcp/"
        delay = _DISCOVER_RETRY_INITIAL_DELAY_SECONDS
        attempt = 1
        while True:
            logger.info("Discovering MCP server %s at %s (attempt %d)", label, url, attempt)
            try:
                return await _exchange(url, settings.MCP_CONNECT_TIMEOUT_SECONDS, _discover(label, url))
            except Exception as exc:  # noqa: BLE001 - anything short of a genuine cancellation (which _exchange lets through as CancelledError, a BaseException) is a failed attempt to retry
                logger.warning(
                    "MCP server %s at %s not reachable yet (%s); retrying in %.1fs",
                    label, url, _describe(exc), delay,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, _DISCOVER_RETRY_MAX_DELAY_SECONDS)
            attempt += 1

    async def close(self) -> None:
        """Nothing to release -- no connection outlives the request that
        opened it. Kept so OrderWorker's lifecycle doesn't need to know that."""

    def list_openai_tools(self) -> list[dict]:
        if guardrails.is_enabled():
            return [tool for tool in self._tools if tool["function"]["name"] not in _DISALLOWED_TOOLS]
        return list(self._tools)

    def server_instructions(self) -> dict[str, str]:
        return {label: server.instructions for label, server in self._servers.items() if server.instructions}

    async def call(self, name: str, arguments: dict) -> str:
        if name in _DISALLOWED_TOOLS and guardrails.is_enabled():
            raise ToolCallError(f"Tool '{name}' is not available to the fulfillment agent")

        label, _, tool_name = name.partition(_TOOL_NAME_SEPARATOR)
        server = self._servers.get(label)
        if server is None:
            raise ToolCallError(f"Unknown tool server '{label}' for tool '{name}'")

        async def call_tool(client: Client):
            return await client.call_tool(tool_name, arguments)

        # Span name is set per-call (rather than via @mlflow.trace, whose name
        # is fixed at decoration time) so each MCP server's calls show up as
        # e.g. "wms__adjust_inventory" in the trace UI instead of every call
        # from every server appearing as the same generic "mcp_tool_call".
        with mlflow.start_span(name=f"mcp__{name}", span_type="TOOL") as span:
            span.set_inputs(arguments)
            try:
                result = await _exchange(
                    server.url,
                    settings.MCP_TOOL_CALL_TIMEOUT_SECONDS,
                    call_tool,
                    mode=server.protocol_version,
                    prior_discover=server.discover,
                )
            except TimeoutError:
                # Without this bound, a downstream server that dies mid-call
                # (e.g. killed by its own liveness probe while inferencing)
                # leaves this await hanging with no exception -- since
                # OrderWorker processes POs serially off one queue, that
                # wedges every subsequent order too, not just this one.
                # Reported distinctly from an unreachable server: the server
                # may just be slow, and the call may have already taken
                # effect on its side, so the model shouldn't be told it's
                # safe to blindly retry.
                logger.warning(
                    "MCP tool %s timed out after %.1fs", name, settings.MCP_TOOL_CALL_TIMEOUT_SECONDS
                )
                raise ToolCallError(
                    f"Tool '{name}' timed out after {settings.MCP_TOOL_CALL_TIMEOUT_SECONDS:.0f}s"
                ) from None
            except MCPError as exc:
                # The server answered, with a protocol-level rejection (a
                # tool it doesn't have, a malformed request) rather than a
                # tool result.
                logger.warning("MCP tool %s was rejected by %s: %s", name, label, exc)
                raise ToolCallError(f"Tool '{name}' was rejected by '{label}': {exc.error.message}") from exc
            except Exception as exc:
                # The request never got an MCP answer -- almost always
                # because the downstream pod is restarting (redeploy/crash/
                # OOM). Nothing to repair on this side: there's no session
                # or connection to re-establish, so the *next* call -- the
                # model's own retry, or a later order -- goes through as
                # soon as the server is back.
                logger.warning("MCP tool %s could not reach %s (%s)", name, label, _describe(exc))
                raise ToolCallError(
                    f"Could not reach '{label}' while calling '{name}'; "
                    "it may be restarting, retry the call."
                ) from exc
            text = "".join(part.text for part in result.content if hasattr(part, "text"))
            if result.is_error:
                logger.warning("MCP tool %s reported an error: %s", name, text)
                raise ToolCallError(text or f"Tool '{name}' failed")
            span.set_outputs(text)
            return text


def _describe(exc: BaseException) -> str:
    """A log-friendly reason for a failed exchange. Transport failures come
    out of the SDK's task group wrapped in an ExceptionGroup whose own message
    ("unhandled errors in a TaskGroup") says nothing about what went wrong."""
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_describe(inner) for inner in exc.exceptions)
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _discover(label: str, url: str) -> Callable[[Client], Awaitable[tuple[_Server, list]]]:
    async def discover(client: Client) -> tuple[_Server, list]:
        discovered = client.session.discover_result
        if discovered is None:
            raise _ServerUnreachableError(
                f"server negotiated session-based protocol {client.protocol_version}, "
                "not the stateless 2026-07-28 revision"
            )
        listed = await client.list_tools()
        server = _Server(
            label=label,
            url=url,
            instructions=client.instructions,
            protocol_version=client.protocol_version,
            discover=discovered,
        )
        return server, listed.tools

    return discover


async def _exchange(
    url: str,
    timeout: float,
    operation: Callable[[Client], Awaitable[Any]],
    **client_options: Any,
) -> Any:
    """Runs one operation against an MCP server through a Client that exists
    only for that operation, bounded by `timeout` seconds end to end.

    Raises TimeoutError if the bound elapses, MCPError if the server answered
    with a protocol error, and some other Exception if it couldn't be reached
    at all. Two details here are load-bearing, both confirmed by reproducing
    the failure they prevent:

    - The bound is anyio.fail_after(), not asyncio.wait_for(): the mcp SDK's
      client is built on anyio task groups/cancel scopes, and
      asyncio.wait_for()'s raw Task.cancel() does not reliably interrupt an
      anyio-scoped await -- a hung request sails past its own timeout.

    - When a server flat-out refuses the connection, the ConnectError happens
      inside a *child* task of the SDK's own task group (its backgrounded
      request sender), not in our foreground await. That child failure
      cancels the task group's scope, which can deliver a bare
      asyncio.CancelledError to the foreground await -- a BaseException, so
      a caller's `except Exception` never sees it, and it would escape all
      the way out of OrderWorker's background task and kill it silently
      (nobody awaits that task to observe the exception). Distinguishing
      that from a *genuine* shutdown request (OrderWorker.stop() cancelling
      this same task, which must be allowed to propagate) needs
      Task.cancelling() (3.11+): >0 means this task itself was asked to
      stop; a CancelledError seen while it's still 0 is necessarily
      incidental to some inner scope, so it's reported as an ordinary
      failure instead."""
    try:
        with anyio.fail_after(timeout):
            async with Client(url, **client_options) as client:
                return await operation(client)
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling() > 0:
            raise
        raise _ServerUnreachableError(f"request to {url} was cancelled by a transport failure") from None
