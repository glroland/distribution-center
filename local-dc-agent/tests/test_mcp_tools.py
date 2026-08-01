from dataclasses import dataclass, field

import pytest

from src.mcp_tools import McpToolRouter, ToolCallError, _Server


@dataclass
class _FakeTextContent:
    text: str


@dataclass
class _FakeCallToolResult:
    content: list
    is_error: bool = False


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
        response=_FakeCallToolResult(content=[_FakeTextContent(text="SKU not found")], is_error=True)
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
