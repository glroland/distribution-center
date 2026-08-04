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
