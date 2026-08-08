import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from src import mcp_tools
from src.mcp_tools import McpToolRouter, ToolCallError, _Server


@dataclass
class _FakeTextContent:
    text: str


@dataclass
class _FakeCallToolResult:
    content: list
    isError: bool = False


@dataclass
class _FakeSession:
    calls: list = field(default_factory=list)
    response: _FakeCallToolResult | None = None

    async def call_tool(self, tool_name: str, arguments: dict) -> _FakeCallToolResult:
        self.calls.append((tool_name, arguments))
        return self.response

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=[])


def _router_with_servers(**servers: tuple[_FakeSession, str | None]) -> McpToolRouter:
    router = McpToolRouter()
    for label, (session, instructions) in servers.items():
        router._servers[label] = _Server(label=label, session=session, instructions=instructions)
    return router


@pytest.mark.asyncio
async def test_call_routes_to_the_right_server() -> None:
    wms_session = _FakeSession(response=_FakeCallToolResult(content=[_FakeTextContent(text='{"ok": true}')]))
    robot_session = _FakeSession(response=_FakeCallToolResult(content=[_FakeTextContent(text="{}")]))
    router = _router_with_servers(wms=(wms_session, None), robot=(robot_session, None))

    result = await router.call("wms__get_inventory_status", {"sku": "SKU-1001"})

    assert result == '{"ok": true}'
    assert wms_session.calls == [("get_inventory_status", {"sku": "SKU-1001"})]
    assert robot_session.calls == []


@pytest.mark.asyncio
async def test_call_raises_on_disallowed_tool() -> None:
    """reset_inventory/reset_robot/reset_shipments exist for demo resets, not
    for the fulfillment LLM to invoke on its own initiative -- call() must
    refuse them even if somehow named directly, as a backstop behind not
    registering them as callable tools in the first place (see the next
    test)."""
    session = _FakeSession(response=_FakeCallToolResult(content=[_FakeTextContent(text="{}")]))
    router = _router_with_servers(wms=(session, None))

    with pytest.raises(ToolCallError, match="not available"):
        await router.call("wms__reset_inventory", {})

    assert session.calls == []  # never reached the downstream server


@pytest.mark.asyncio
async def test_call_raises_on_unknown_server() -> None:
    router = _router_with_servers()

    with pytest.raises(ToolCallError):
        await router.call("nonexistent__do_thing", {})


@pytest.mark.asyncio
async def test_call_raises_tool_call_error_on_error_result() -> None:
    session = _FakeSession(
        response=_FakeCallToolResult(content=[_FakeTextContent(text="SKU not found")], isError=True)
    )
    router = _router_with_servers(wms=(session, None))

    with pytest.raises(ToolCallError, match="SKU not found"):
        await router.call("wms__get_inventory_status", {"sku": "does-not-exist"})


@pytest.mark.asyncio
async def test_register_excludes_disallowed_tools_from_registration() -> None:
    """reset_inventory must never even appear in list_openai_tools() -- the
    fulfillment LLM shouldn't be offered it as an option in the first place,
    rather than relying only on call()'s runtime refusal."""
    listed_tools = SimpleNamespace(
        tools=[
            SimpleNamespace(name="get_inventory_status", description="", inputSchema={}),
            SimpleNamespace(name="reset_inventory", description="", inputSchema={}),
        ]
    )
    session = SimpleNamespace(list_tools=lambda: _async_result(listed_tools))
    router = McpToolRouter()

    async def fake_connect_with_retry(label: str, base_url: str):
        return session, None

    router._connect_with_retry = fake_connect_with_retry
    await router._register("wms", "http://wms")

    names = [t["function"]["name"] for t in router.list_openai_tools()]
    assert names == ["wms__get_inventory_status"]
    assert "wms__reset_inventory" not in names


async def _async_result(value):
    return value


def test_list_openai_tools_are_prefixed_by_server() -> None:
    router = McpToolRouter()
    router._tools = [
        {"type": "function", "function": {"name": "wms__get_location", "description": "", "parameters": {}}},
        {"type": "function", "function": {"name": "robot__move_robot", "description": "", "parameters": {}}},
    ]

    names = [t["function"]["name"] for t in router.list_openai_tools()]

    assert names == ["wms__get_location", "robot__move_robot"]


def test_server_instructions_omits_servers_without_instructions() -> None:
    router = _router_with_servers(
        wms=(_FakeSession(), "manage inventory"),
        robot=(_FakeSession(), None),
    )

    assert router.server_instructions() == {"wms": "manage inventory"}


class _FakeTransportContext:
    """Stands in for streamable_http_client(url)'s async context manager."""

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def __aenter__(self) -> tuple[str, str, None]:
        self._calls.append("transport_enter")
        return ("read-stream", "write-stream", None)

    async def __aexit__(self, *_exc) -> bool:
        self._calls.append("transport_exit")
        return False


def _make_fake_session_cls(fail_times: list[int], calls: list[str]):
    """Stands in for mcp.ClientSession: the first `fail_times` initialize()
    calls raise (simulating the MCP endpoint not being up yet, e.g. a 404
    while the app it's mounted in is still starting), then it succeeds."""

    class _FakeSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeSession":
            calls.append("session_enter")
            return self

        async def __aexit__(self, *_exc) -> bool:
            calls.append("session_exit")
            return False

        async def initialize(self) -> SimpleNamespace:
            if fail_times[0] > 0:
                fail_times[0] -= 1
                raise ConnectionError("mcp endpoint not mounted yet")
            return SimpleNamespace(instructions="do the thing")

    return _FakeSession


@pytest.mark.asyncio
async def test_connect_with_retry_backs_off_until_server_becomes_reachable(monkeypatch) -> None:
    calls: list[str] = []
    fail_times = [2]  # first two attempts fail, third succeeds
    sleeps: list[float] = []

    monkeypatch.setattr(mcp_tools, "streamable_http_client", lambda url: _FakeTransportContext(calls))
    monkeypatch.setattr(mcp_tools, "ClientSession", _make_fake_session_cls(fail_times, calls))

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(mcp_tools.asyncio, "sleep", fake_sleep)

    router = McpToolRouter()
    session, instructions = await router._connect_with_retry("wms", "http://wms")

    assert instructions == "do the thing"
    assert sleeps == [1.0, 2.0]  # capped exponential backoff, not given up on
    assert len(router._server_stacks) == 1  # only the successful attempt is kept open
    assert calls.count("transport_enter") == 3
    assert calls.count("session_enter") == 3
    # the two failed attempts were torn down immediately, not leaked for the
    # router's lifetime
    assert calls.count("transport_exit") == 2
    assert calls.count("session_exit") == 2


@pytest.mark.asyncio
async def test_call_reconnects_when_the_connection_dies() -> None:
    """Simulates a downstream pod restart severing the persistent MCP
    session mid-lifetime (not at startup). Before the fix, every later call
    to that server would fail the same way forever -- only bouncing the
    dc-agent pod itself re-ran connect(). call() must instead reconnect the
    affected server so the *next* call succeeds."""

    async def broken_call_tool(tool_name: str, arguments: dict) -> _FakeCallToolResult:
        raise RuntimeError("peer closed the connection")

    dead_session = _FakeSession()
    dead_session.call_tool = broken_call_tool

    fresh_session = _FakeSession(response=_FakeCallToolResult(content=[_FakeTextContent(text="{}")]))

    router = _router_with_servers(wms=(dead_session, "manage inventory"))
    router._server_urls["wms"] = "http://wms"

    reconnect_calls: list[tuple[str, str]] = []

    async def fake_connect_with_retry(label: str, base_url: str):
        reconnect_calls.append((label, base_url))
        return fresh_session, "manage inventory"

    router._connect_with_retry = fake_connect_with_retry

    with pytest.raises(ToolCallError, match="Lost connection"):
        await router.call("wms__get_inventory_status", {"sku": "SKU-1001"})

    assert reconnect_calls == [("wms", "http://wms")]
    assert router._servers["wms"].session is fresh_session
    assert fresh_session.calls == []  # the failed call itself was not retried automatically

    # a subsequent call -- the model's own retry -- goes through the reconnected session
    result = await router.call("wms__get_inventory_status", {"sku": "SKU-1001"})

    assert result == "{}"
    assert fresh_session.calls == [("get_inventory_status", {"sku": "SKU-1001"})]


@pytest.mark.asyncio
async def test_call_does_not_reconnect_on_timeout() -> None:
    """A timeout is ambiguous -- the server may just be slow, and the call
    may already have taken effect on its side -- so unlike a hard connection
    failure it must not trigger a reconnect."""

    async def slow_call_tool(tool_name: str, arguments: dict) -> _FakeCallToolResult:
        await asyncio.sleep(10)
        return _FakeCallToolResult(content=[])

    session = _FakeSession()
    session.call_tool = slow_call_tool

    router = _router_with_servers(wms=(session, None))
    mcp_tools.settings.MCP_TOOL_CALL_TIMEOUT_SECONDS = 0.01
    try:
        with pytest.raises(ToolCallError, match="timed out"):
            await router.call("wms__get_inventory_status", {"sku": "SKU-1001"})
    finally:
        mcp_tools.settings.MCP_TOOL_CALL_TIMEOUT_SECONDS = 60.0

    assert router._servers["wms"].session is session  # untouched, no reconnect attempted


@pytest.mark.asyncio
async def test_reconnect_closes_the_old_stack_before_reconnecting() -> None:
    closed: list[str] = []

    class _FakeStack:
        async def aclose(self) -> None:
            closed.append("closed")

    router = McpToolRouter()
    router._server_urls["wms"] = "http://wms"
    router._server_stacks["wms"] = _FakeStack()

    new_session = _FakeSession()

    async def fake_connect_with_retry(label: str, base_url: str):
        assert closed == ["closed"]  # old connection torn down before the new one is opened
        return new_session, None

    router._connect_with_retry = fake_connect_with_retry

    await router._reconnect("wms")

    assert closed == ["closed"]
    assert router._servers["wms"].session is new_session
