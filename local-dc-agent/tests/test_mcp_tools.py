import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from mcp import MCPError

from src import guardrails, mcp_tools
from src.mcp_tools import McpToolRouter, ToolCallError, _Server


@dataclass
class _FakeTextContent:
    text: str


@dataclass
class _FakeCallToolResult:
    content: list
    is_error: bool = False


@dataclass
class _FakeServer:
    """Stands in for one downstream MCP server: what it answers, and a record
    of every request that reached it."""

    calls: list = field(default_factory=list)
    response: _FakeCallToolResult | None = None
    tools: list = field(default_factory=list)
    instructions: str | None = "do the thing"
    protocol_version: str = "2026-07-28"
    # None is what the SDK reports after falling back to a handshake-era
    # (session-based) server that doesn't answer server/discover.
    discover_result: object | None = "discover-result"
    # Raised from Client.__aenter__ (once per entry in the list) to simulate
    # the server being down/refusing connections.
    enter_errors: list = field(default_factory=list)
    call_error: BaseException | None = None
    hang_enters: int = 0
    hang_calls: bool = False
    clients_opened: list = field(default_factory=list)
    clients_closed: int = 0


def _install_fake_client(monkeypatch, **servers: _FakeServer) -> None:
    """Replaces mcp_tools.Client with a fake keyed by URL
    (http://<label>/mcp/), mirroring the real one's contract: an async
    context manager that exists for exactly one exchange."""

    class _FakeClient:
        def __init__(self, url: str, **options) -> None:
            label = url.removeprefix("http://").split("/")[0]
            self._server = servers[label]
            self._server.clients_opened.append({"url": url, **options})
            self.session = SimpleNamespace(discover_result=self._server.discover_result)
            self.instructions = self._server.instructions
            self.protocol_version = self._server.protocol_version

        async def __aenter__(self) -> "_FakeClient":
            if self._server.hang_enters > 0:
                self._server.hang_enters -= 1
                await asyncio.Event().wait()  # never set -- hangs forever, unlike asyncio.sleep
            if self._server.enter_errors:
                raise self._server.enter_errors.pop(0)
            return self

        async def __aexit__(self, *_exc) -> bool:
            self._server.clients_closed += 1
            return False

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=self._server.tools)

        async def call_tool(self, tool_name: str, arguments: dict) -> _FakeCallToolResult:
            self._server.calls.append((tool_name, arguments))
            if self._server.hang_calls:
                await asyncio.Event().wait()
            if self._server.call_error is not None:
                raise self._server.call_error
            return self._server.response

    monkeypatch.setattr(mcp_tools, "Client", _FakeClient)


def _router_with_servers(monkeypatch, **servers: _FakeServer) -> McpToolRouter:
    """A router that has already discovered the given servers."""
    _install_fake_client(monkeypatch, **servers)
    router = McpToolRouter()
    for label, server in servers.items():
        router._servers[label] = _Server(
            label=label,
            url=f"http://{label}/mcp/",
            instructions=server.instructions,
            protocol_version=server.protocol_version,
            discover=server.discover_result,
        )
    return router


def _text_result(text: str, is_error: bool = False) -> _FakeCallToolResult:
    return _FakeCallToolResult(content=[_FakeTextContent(text=text)], is_error=is_error)


@pytest.fixture
def no_backoff_sleep(monkeypatch) -> list[float]:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(mcp_tools.asyncio, "sleep", fake_sleep)
    return sleeps


@pytest.mark.asyncio
async def test_call_routes_to_the_right_server(monkeypatch) -> None:
    wms = _FakeServer(response=_text_result('{"ok": true}'))
    robot = _FakeServer(response=_text_result("{}"))
    router = _router_with_servers(monkeypatch, wms=wms, robot=robot)

    result = await router.call("wms__get_inventory_status", {"sku": "SKU-1001"})

    assert result == '{"ok": true}'
    assert wms.calls == [("get_inventory_status", {"sku": "SKU-1001"})]
    assert robot.calls == []


@pytest.mark.asyncio
async def test_each_call_is_a_self_contained_stateless_exchange(monkeypatch) -> None:
    """No connection or session is held between calls: every call opens its
    own Client and closes it again, pinned to the protocol revision learned
    at discovery so it's a single request with no handshake/discover round
    trip of its own."""
    wms = _FakeServer(response=_text_result("{}"))
    router = _router_with_servers(monkeypatch, wms=wms)

    await router.call("wms__get_location", {})
    await router.call("wms__get_location", {})

    assert wms.clients_opened == [
        {"url": "http://wms/mcp/", "mode": "2026-07-28", "prior_discover": "discover-result"},
        {"url": "http://wms/mcp/", "mode": "2026-07-28", "prior_discover": "discover-result"},
    ]
    assert wms.clients_closed == 2


@pytest.mark.asyncio
async def test_call_raises_on_disallowed_tool(monkeypatch) -> None:
    """reset_inventory/reset_robot/reset_shipments exist for demo resets, not
    for the fulfillment LLM to invoke on its own initiative -- call() must
    refuse them even if somehow named directly, as a backstop behind not
    registering them as callable tools in the first place (see the next
    test)."""
    wms = _FakeServer(response=_text_result("{}"))
    router = _router_with_servers(monkeypatch, wms=wms)

    with pytest.raises(ToolCallError, match="not available"):
        await router.call("wms__reset_inventory", {})

    assert wms.calls == []  # never reached the downstream server
    assert wms.clients_opened == []


@pytest.mark.asyncio
async def test_call_allows_disallowed_tool_when_agentic_safety_is_off(monkeypatch) -> None:
    guardrails.set_enabled(False)
    wms = _FakeServer(response=_text_result('{"status": "ok"}'))
    router = _router_with_servers(monkeypatch, wms=wms)

    result = await router.call("wms__reset_inventory", {})

    assert result == '{"status": "ok"}'
    assert wms.calls == [("reset_inventory", {})]


@pytest.mark.asyncio
async def test_call_raises_on_unknown_server(monkeypatch) -> None:
    router = _router_with_servers(monkeypatch)

    with pytest.raises(ToolCallError):
        await router.call("nonexistent__do_thing", {})


@pytest.mark.asyncio
async def test_call_raises_tool_call_error_on_error_result(monkeypatch) -> None:
    wms = _FakeServer(response=_text_result("SKU not found", is_error=True))
    router = _router_with_servers(monkeypatch, wms=wms)

    with pytest.raises(ToolCallError, match="SKU not found"):
        await router.call("wms__get_inventory_status", {"sku": "does-not-exist"})


@pytest.mark.asyncio
async def test_call_raises_tool_call_error_on_protocol_rejection(monkeypatch) -> None:
    """A JSON-RPC error (as opposed to an is_error tool result) is raised by
    the SDK as MCPError; the model still gets the server's own message."""
    wms = _FakeServer(call_error=MCPError(-32602, "Invalid params: delta"))
    router = _router_with_servers(monkeypatch, wms=wms)

    with pytest.raises(ToolCallError, match="rejected by 'wms': Invalid params: delta"):
        await router.call("wms__adjust_inventory", {"sku": "SKU-1001"})


@pytest.mark.asyncio
async def test_register_excludes_disallowed_tools_from_registration(monkeypatch) -> None:
    """reset_inventory must never even appear in list_openai_tools() -- the
    fulfillment LLM shouldn't be offered it as an option in the first place,
    rather than relying only on call()'s runtime refusal."""
    wms = _FakeServer(
        tools=[
            SimpleNamespace(name="get_inventory_status", description="", input_schema={}),
            SimpleNamespace(name="reset_inventory", description="", input_schema={}),
        ]
    )
    _install_fake_client(monkeypatch, wms=wms)
    router = McpToolRouter()

    await router._register("wms", "http://wms")

    names = [t["function"]["name"] for t in router.list_openai_tools()]
    assert names == ["wms__get_inventory_status"]
    assert "wms__reset_inventory" not in names


@pytest.mark.asyncio
async def test_register_still_discovers_disallowed_tools_for_later_toggle(monkeypatch) -> None:
    """reset_inventory is hidden from list_openai_tools() while enabled (see
    above), but must still be discovered and kept in the router's own state
    at registration time -- otherwise flipping Agentic Safety off wouldn't
    re-expose it without rediscovering the server."""
    wms = _FakeServer(
        tools=[
            SimpleNamespace(name="get_inventory_status", description="", input_schema={}),
            SimpleNamespace(name="reset_inventory", description="", input_schema={}),
        ]
    )
    _install_fake_client(monkeypatch, wms=wms)
    router = McpToolRouter()

    await router._register("wms", "http://wms")

    guardrails.set_enabled(False)
    names = [t["function"]["name"] for t in router.list_openai_tools()]
    assert names == ["wms__get_inventory_status", "wms__reset_inventory"]


@pytest.mark.asyncio
async def test_register_keeps_no_connection_open(monkeypatch) -> None:
    """Discovery is itself one closed exchange: what it learned is kept, the
    Client it used is not."""
    wms = _FakeServer(tools=[SimpleNamespace(name="get_location", description="where", input_schema={"type": "object"})])
    _install_fake_client(monkeypatch, wms=wms)
    router = McpToolRouter()

    await router._register("wms", "http://wms")

    assert wms.clients_opened == [{"url": "http://wms/mcp/"}]  # auto-negotiated: server/discover, no pinned mode yet
    assert wms.clients_closed == 1
    assert router.server_instructions() == {"wms": "do the thing"}
    assert router.list_openai_tools() == [
        {"type": "function", "function": {"name": "wms__get_location", "description": "where", "parameters": {"type": "object"}}}
    ]
    await router.close()  # nothing to release, must not raise


def test_list_openai_tools_are_prefixed_by_server() -> None:
    router = McpToolRouter()
    router._tools = [
        {"type": "function", "function": {"name": "wms__get_location", "description": "", "parameters": {}}},
        {"type": "function", "function": {"name": "robot__move_robot", "description": "", "parameters": {}}},
    ]

    names = [t["function"]["name"] for t in router.list_openai_tools()]

    assert names == ["wms__get_location", "robot__move_robot"]


def test_server_instructions_omits_servers_without_instructions(monkeypatch) -> None:
    router = _router_with_servers(
        monkeypatch,
        wms=_FakeServer(instructions="manage inventory"),
        robot=_FakeServer(instructions=None),
    )

    assert router.server_instructions() == {"wms": "manage inventory"}


@pytest.mark.asyncio
async def test_discover_with_retry_backs_off_until_server_becomes_reachable(monkeypatch, no_backoff_sleep) -> None:
    # first two attempts fail (the second the way a refused connection really
    # surfaces from the SDK: an ExceptionGroup out of its task group), third
    # succeeds
    wms = _FakeServer(
        enter_errors=[
            ConnectionError("mcp endpoint not mounted yet"),
            ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("All connection attempts failed")]),
        ]
    )
    _install_fake_client(monkeypatch, wms=wms)

    router = McpToolRouter()
    server, tools = await router._discover_with_retry("wms", "http://wms")

    assert server.instructions == "do the thing"
    assert server.url == "http://wms/mcp/"
    assert no_backoff_sleep == [1.0, 2.0]  # capped exponential backoff, not given up on
    assert len(wms.clients_opened) == 3


@pytest.mark.asyncio
async def test_discover_with_retry_times_out_a_hung_attempt(monkeypatch, no_backoff_sleep) -> None:
    """A downstream server whose pod/process is up and accepting TCP
    connections but hasn't finished its own startup yet (e.g. label-api still
    loading its ML checkpoints) can leave the request hanging with no
    exception. Without a bound on the attempt, that hang wedges connect()
    (and the whole OrderWorker) permanently instead of the retry-with-backoff
    loop ever getting a chance to run again."""
    label = _FakeServer(hang_enters=1)  # first attempt hangs past the timeout, second succeeds
    _install_fake_client(monkeypatch, label=label)
    monkeypatch.setattr(mcp_tools.settings, "MCP_CONNECT_TIMEOUT_SECONDS", 0.01)

    router = McpToolRouter()
    server, tools = await router._discover_with_retry("label", "http://label")

    assert server.instructions == "do the thing"
    assert no_backoff_sleep == [1.0]  # one retry after the timed-out attempt
    assert len(label.clients_opened) == 2


@pytest.mark.asyncio
async def test_discover_with_retry_survives_an_incidental_cancellation(monkeypatch, no_backoff_sleep) -> None:
    """A refused connection can reach the foreground await as a bare
    CancelledError (the SDK's task group cancelling its own scope after a
    child task failed) rather than as an exception. That must count as a
    failed attempt -- propagating it would silently kill OrderWorker's
    background task even though nobody asked it to stop."""
    wms = _FakeServer(enter_errors=[asyncio.CancelledError()])
    _install_fake_client(monkeypatch, wms=wms)

    router = McpToolRouter()
    server, tools = await router._discover_with_retry("wms", "http://wms")

    assert server.instructions == "do the thing"
    assert no_backoff_sleep == [1.0]


@pytest.mark.asyncio
async def test_discover_with_retry_propagates_a_genuine_cancellation(monkeypatch) -> None:
    """OrderWorker.stop() cancelling the task mid-discovery must actually
    stop it, not be swallowed into another retry."""
    wms = _FakeServer(hang_enters=1)
    _install_fake_client(monkeypatch, wms=wms)

    router = McpToolRouter()
    task = asyncio.create_task(router._discover_with_retry("wms", "http://wms"))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(wms.clients_opened) == 1


@pytest.mark.asyncio
async def test_discover_with_retry_rejects_a_session_based_server(monkeypatch, no_backoff_sleep) -> None:
    """A server that only speaks a handshake-era protocol revision would need
    a session held open for it; this router never holds one, so such a server
    is retried like one that isn't up yet rather than quietly used statefully."""
    legacy = _FakeServer(protocol_version="2025-11-25", discover_result=None)
    _install_fake_client(monkeypatch, wms=legacy)

    async def upgrade_then_sleep(delay: float) -> None:
        no_backoff_sleep.append(delay)
        legacy.protocol_version = "2026-07-28"
        legacy.discover_result = "discover-result"

    monkeypatch.setattr(mcp_tools.asyncio, "sleep", upgrade_then_sleep)

    router = McpToolRouter()
    server, tools = await router._discover_with_retry("wms", "http://wms")

    assert no_backoff_sleep == [1.0]
    assert server.protocol_version == "2026-07-28"


@pytest.mark.asyncio
async def test_call_recovers_by_itself_after_the_server_was_unreachable(monkeypatch) -> None:
    """Simulates a downstream pod restart mid-lifetime (not at startup).
    There is no session or connection for the restart to break: the call
    that lands during the outage fails with a retryable error, and the next
    one simply goes through -- no reconnect step, no dc-agent restart."""
    wms = _FakeServer(
        response=_text_result("{}"),
        enter_errors=[
            ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("All connection attempts failed")])
        ],
    )
    router = _router_with_servers(monkeypatch, wms=wms)
    registered = router._servers["wms"]

    with pytest.raises(ToolCallError, match="Could not reach 'wms'.*retry the call"):
        await router.call("wms__get_inventory_status", {"sku": "SKU-1001"})

    assert wms.calls == []  # the failed call itself was not retried automatically
    assert router._servers["wms"] is registered  # nothing was torn down or rebuilt

    # a subsequent call -- the model's own retry -- reaches the restarted server
    result = await router.call("wms__get_inventory_status", {"sku": "SKU-1001"})

    assert result == "{}"
    assert wms.calls == [("get_inventory_status", {"sku": "SKU-1001"})]


@pytest.mark.asyncio
async def test_call_reports_an_incidental_cancellation_as_unreachable(monkeypatch) -> None:
    wms = _FakeServer(call_error=asyncio.CancelledError())
    router = _router_with_servers(monkeypatch, wms=wms)

    with pytest.raises(ToolCallError, match="Could not reach 'wms'"):
        await router.call("wms__get_location", {})


@pytest.mark.asyncio
async def test_call_times_out_a_hung_server(monkeypatch) -> None:
    """A timeout is ambiguous -- the server may just be slow, and the call
    may already have taken effect on its side -- so unlike an unreachable
    server it must not be reported as safe to retry."""
    wms = _FakeServer(hang_calls=True)
    router = _router_with_servers(monkeypatch, wms=wms)
    monkeypatch.setattr(mcp_tools.settings, "MCP_TOOL_CALL_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(ToolCallError, match="timed out") as excinfo:
        await router.call("wms__get_inventory_status", {"sku": "SKU-1001"})

    assert "retry" not in str(excinfo.value)
    assert wms.clients_closed == 1  # the timed-out exchange was still closed, not leaked


def test_describe_unwraps_the_sdk_task_group_wrapper() -> None:
    wrapped = ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("All connection attempts failed")])

    assert mcp_tools._describe(wrapped) == "ConnectionError: All connection attempts failed"
    assert mcp_tools._describe(TimeoutError()) == "TimeoutError"
