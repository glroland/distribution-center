import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass

import anyio
import mlflow
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

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
# self._tools either way so flipping the toggle off doesn't require a
# reconnect to re-expose them.
_DISALLOWED_TOOLS = {"wms__reset_inventory", "robot__reset_robot", "shipping__reset_shipments"}
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
    server. Connections are opened once and reused for the router's lifetime,
    but a server whose connection dies underneath us (e.g. its pod restarted
    and dropped the streamable-HTTP session) is transparently reconnected by
    call() rather than left broken for the rest of this process's life -- see
    call()'s comment for why that used to require a manual dc-agent restart."""

    def __init__(self) -> None:
        self._server_urls: dict[str, str] = {}
        # Each server's transport/session context is kept in its own stack,
        # keyed by label so a single server can be torn down and replaced
        # independently on reconnect (see _reconnect) without touching the
        # others. A stack is stored only once that server's connection
        # attempt actually succeeds (see _connect_with_retry) -- that way a
        # failed attempt never leaks a half-open connection into the
        # router's lifetime.
        self._server_stacks: dict[str, AsyncExitStack] = {}
        self._servers: dict[str, _Server] = {}
        self._tools: list[dict] = []

    async def connect(self) -> None:
        self._server_urls = {
            "wms": settings.WMS_API_URL,
            "robot": settings.ROBOT_API_URL,
            "shipping": settings.SHIPPING_API_URL,
            "supervisor": settings.SUPERVISOR_API_URL,
            "label": settings.LABEL_API_URL,
        }
        for label, base_url in self._server_urls.items():
            await self._register(label, base_url)

    async def _register(self, label: str, base_url: str) -> None:
        """Connects (or reconnects) one server and (re)registers its tools.
        Safe to call again for a label that's already registered -- any
        stale `label__*` entries in the tool list are replaced rather than
        duplicated, since a reconnect may hit a server whose tool set
        changed (e.g. it was redeployed with new code)."""
        session, instructions, listed_tools = await self._connect_with_retry(label, base_url)
        self._servers[label] = _Server(label=label, session=session, instructions=instructions)

        prefix = f"{label}{_TOOL_NAME_SEPARATOR}"
        self._tools = [tool for tool in self._tools if not tool["function"]["name"].startswith(prefix)]
        for tool in listed_tools:
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
        disallowed_count = sum(1 for tool in listed_tools if f"{label}{_TOOL_NAME_SEPARATOR}{tool.name}" in _DISALLOWED_TOOLS)
        logger.info(
            "Connected to %s: %d tool(s) registered (%d hidden while Agentic Safety is on)",
            label, len(listed_tools), disallowed_count,
        )

    async def _connect_with_retry(self, label: str, base_url: str) -> tuple[ClientSession, str | None, list]:
        """Connects to one MCP server, retrying with capped exponential backoff
        until it succeeds. Never gives up -- an unreachable server here is
        assumed to be a start-up ordering race (server not scheduled yet, still
        booting, etc.) rather than a permanent misconfiguration, so this blocks
        the caller rather than raising and killing the whole agent.

        The handshake (initialize) and the first list_tools() call are both
        bounded by MCP_CONNECT_TIMEOUT_SECONDS and done here, inside the retry
        loop, rather than left to the caller: a server whose process is up and
        accepting TCP connections but hasn't actually started reading from the
        socket yet (e.g. label-api/wms still loading data/model files before
        uvicorn's event loop is running) leaves the connection sitting in the
        kernel's accept queue -- our request goes out, but nothing ever reads
        or responds to it, even after that server finishes starting, since by
        then it's on to serving newer connections rather than draining stale
        ones. The MCP client's HTTP transport has no read timeout of its own
        (a long-lived SSE stream needs none), so without a bound here this
        wedges the attempt forever, since the except-and-retry below only
        runs on an exception, never on a hang. This is bounded with
        anyio.fail_after() rather than asyncio.wait_for(): the mcp SDK's
        ClientSession is itself built on anyio task groups/cancel scopes, and
        asyncio.wait_for()'s raw Task.cancel() does not reliably interrupt an
        anyio-scoped await -- confirmed by reproducing this exact hang with
        asyncio.wait_for() in place and watching it sail past its own
        timeout with no warning log.

        A second, independent failure mode lives here too, also confirmed by
        reproduction: when the server flat-out refuses the connection (e.g.
        its port isn't listening yet at all) rather than merely being slow,
        the ConnectError happens inside a *child* task of the mcp SDK's own
        anyio task group (the backgrounded request sender), not in our
        foreground await directly. That child failure cancels the task
        group's scope, which delivers a bare asyncio.CancelledError to our
        foreground await -- a BaseException, so a plain `except Exception`
        never sees it; it skips straight past to a bare `finally`, where
        attempt_stack.aclose() re-raises the *real* underlying error as an
        ExceptionGroup that then escapes this function entirely, silently
        killing OrderWorker's background task (nobody awaits it to observe
        the exception) -- the same "narrow except clause" failure class as
        the past bug documented in worker.py's history, just via anyio's
        cancellation semantics instead of a typo. Distinguishing that from a
        *genuine* shutdown request (OrderWorker.stop() cancelling this same
        task, which also arrives as CancelledError and must be allowed to
        propagate) needs Task.cancelling() (3.11+): >0 means this task
        itself was asked to stop; a CancelledError seen while it's still 0
        is necessarily incidental to some inner scope, safe to treat as a
        failed attempt and retry."""
        delay = _CONNECT_RETRY_INITIAL_DELAY_SECONDS
        attempt = 1
        while True:
            attempt_stack = AsyncExitStack()
            succeeded = False
            caught: BaseException | None = None
            try:
                logger.info("Connecting to MCP server %s at %s (attempt %d)", label, base_url, attempt)
                read_stream, write_stream, _ = await attempt_stack.enter_async_context(
                    streamable_http_client(f"{base_url}/mcp")
                )
                session = await attempt_stack.enter_async_context(ClientSession(read_stream, write_stream))
                with anyio.fail_after(settings.MCP_CONNECT_TIMEOUT_SECONDS):
                    init_result = await session.initialize()
                with anyio.fail_after(settings.MCP_CONNECT_TIMEOUT_SECONDS):
                    listed = await session.list_tools()
                succeeded = True
            except BaseException as exc:  # noqa: BLE001 - see Task.cancelling() note above on why this is deliberately broad
                caught = exc

            if not succeeded:
                try:
                    await attempt_stack.aclose()
                except BaseException as close_exc:  # noqa: BLE001 - closing a failed attempt can itself raise the real underlying error (see docstring); still not a reason to skip the retry-vs-propagate decision below
                    caught = close_exc

            if succeeded:
                self._server_stacks[label] = attempt_stack
                return session, init_result.instructions, listed.tools

            if isinstance(caught, asyncio.CancelledError) and asyncio.current_task().cancelling() > 0:
                raise caught

            logger.warning(
                "MCP server %s at %s not reachable yet (%s); retrying in %.1fs",
                label, base_url, caught, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, _CONNECT_RETRY_MAX_DELAY_SECONDS)
            attempt += 1

    async def _reconnect(self, label: str) -> None:
        """Replaces one server's connection after call() finds it's no longer
        usable. Closes the old (broken) stack before reconnecting so it isn't
        leaked, then reuses _connect_with_retry's retry-with-backoff -- the
        same logic that already handles a downstream server being briefly
        unreachable at startup applies just as well to a mid-life outage.

        Must run in the task that owns the router (OrderWorker's single
        background task, same as connect()/close()) rather than a spawned
        one: anyio's cancel scopes require being entered and exited from the
        same asyncio task, so closing attempt_stack from a different task
        would raise RuntimeError (see the same-task note in
        OrderWorker.stop()). call() satisfies this because it's always
        awaited directly from that task, never scheduled separately."""
        old_stack = self._server_stacks.pop(label, None)
        if old_stack is not None:
            try:
                await old_stack.aclose()
            except Exception:
                logger.exception("Error closing stale MCP connection to %s", label)
        await self._register(label, self._server_urls[label])
        logger.info("Reconnected to MCP server %s", label)

    async def close(self) -> None:
        for label in list(self._server_stacks):
            stack = self._server_stacks.pop(label)
            await stack.aclose()

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

        # Span name is set per-call (rather than via @mlflow.trace, whose name
        # is fixed at decoration time) so each MCP server's calls show up as
        # e.g. "wms__adjust_inventory" in the trace UI instead of every call
        # from every server appearing as the same generic "mcp_tool_call".
        with mlflow.start_span(name=f"mcp__{name}", span_type="TOOL") as span:
            span.set_inputs(arguments)
            try:
                # anyio.fail_after(), not asyncio.wait_for(): ClientSession is
                # built on anyio task groups/cancel scopes, and
                # asyncio.wait_for()'s raw Task.cancel() does not reliably
                # interrupt an anyio-scoped await -- see the longer note on
                # this in _connect_with_retry, where the same swap was needed
                # to make a connect-time hang actually time out instead of
                # sailing past its own deadline.
                with anyio.fail_after(settings.MCP_TOOL_CALL_TIMEOUT_SECONDS):
                    result = await server.session.call_tool(tool_name, arguments)
            except TimeoutError:
                # Without this, a downstream server that dies mid-call (e.g.
                # killed by its own liveness probe while inferencing) leaves
                # this await hanging forever with no exception -- since
                # OrderWorker processes POs serially off one queue, that
                # wedges every subsequent order too, not just this one.
                # Deliberately not treated as a dead connection (no
                # reconnect below): the server may just be slow, and the
                # call may have already taken effect on its side, so
                # tearing down the session here wouldn't be safe to do
                # automatically.
                logger.warning(
                    "MCP tool %s timed out after %.1fs", name, settings.MCP_TOOL_CALL_TIMEOUT_SECONDS
                )
                raise ToolCallError(
                    f"Tool '{name}' timed out after {settings.MCP_TOOL_CALL_TIMEOUT_SECONDS:.0f}s"
                ) from None
            except Exception as exc:
                # The persistent session to this server (opened once in
                # connect() and, until now, never revisited) has died
                # underneath us -- almost always because the downstream
                # pod restarted (redeploy/crash/OOM) and the streamable-HTTP
                # session it held server-side (in-memory, like all state in
                # this repo's services) is gone. Concretely this surfaces as
                # mcp.shared.exceptions.McpError (the SDK's own receive loop
                # noticing the stream closed) or anyio.ClosedResourceError
                # (writing to a stream that receive loop already tore down).
                # Left alone, every later call to this server fails the same
                # way for the rest of this pod's life -- fulfillment just
                # quietly escalates every order -- and only bouncing the
                # dc-agent pod itself (which reruns connect()) ever
                # recovers. Reconnect now instead, so the *next* call --
                # the model's own retry, or a later order -- goes through a
                # working session.
                logger.warning(
                    "MCP tool %s lost its connection to %s (%s); reconnecting",
                    name, label, exc,
                )
                await self._reconnect(label)
                raise ToolCallError(
                    f"Lost connection to '{label}' while calling '{name}'; "
                    "the connection has been re-established, retry the call."
                ) from exc
            text = "".join(part.text for part in result.content if hasattr(part, "text"))
            if result.isError:
                logger.warning("MCP tool %s reported an error: %s", name, text)
                raise ToolCallError(text or f"Tool '{name}' failed")
            span.set_outputs(text)
            return text
