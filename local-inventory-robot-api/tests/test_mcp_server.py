import asyncio
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.app import mcp_server, robot


@pytest.fixture(autouse=True)
def _reset_robot():
    robot.reset()
    yield
    robot.reset()


def _call(tool: str, args: dict) -> dict:
    result = asyncio.run(mcp_server.call_tool(tool, args))
    text = "".join(part.text for part in result.content if hasattr(part, "text"))
    return json.loads(text)


def test_get_robot_status_tool() -> None:
    body = _call("get_robot_status", {})
    assert body == {"x": 0, "y": 0, "carrying": {}, "capacity": 100, "carrying_total": 0}


def test_find_item_tool() -> None:
    body = _call("find_item", {"sku": "SKU-1001"})
    locations = {(loc["location_x"], loc["location_y"]): loc["qty"] for loc in body["locations"]}
    assert locations == {(3, 5): 50, (6, 6): 10}


def test_find_item_tool_unknown_sku_returns_empty() -> None:
    body = _call("find_item", {"sku": "does-not-exist"})
    assert body["locations"] == []


def test_get_shelf_inventory_at_location() -> None:
    body = _call("get_shelf_inventory", {"x": 7, "y": 2})
    assert body == {"location_x": 7, "location_y": 2, "stock": {"SKU-1002": 20}}


def test_get_shelf_inventory_at_current_location() -> None:
    _call("move_robot", {"x": 1, "y": 8})
    body = _call("get_shelf_inventory", {})
    assert body == {"location_x": 1, "location_y": 8, "stock": {"SKU-1003": 15}}


def test_move_robot_tool() -> None:
    body = _call("move_robot", {"x": 3, "y": 5})
    assert (body["x"], body["y"]) == (3, 5)


def test_move_robot_out_of_bounds_raises() -> None:
    with pytest.raises(ToolError):
        _call("move_robot", {"x": 99, "y": 0})


def test_fetch_item_tool() -> None:
    _call("move_robot", {"x": 3, "y": 5})
    body = _call("fetch_item", {"sku": "SKU-1001", "qty": 10})
    assert body["carrying"] == {"SKU-1001": 10}


def test_fetch_item_unknown_sku_raises() -> None:
    _call("move_robot", {"x": 3, "y": 5})
    with pytest.raises(ToolError):
        _call("fetch_item", {"sku": "does-not-exist", "qty": 1})


def test_deliver_items_round_trip() -> None:
    _call("move_robot", {"x": 3, "y": 5})
    _call("fetch_item", {"sku": "SKU-1001", "qty": 10})
    _call("move_robot", {"x": 0, "y": 0})
    body = _call("deliver_items", {})
    assert body["delivered"] == {"SKU-1001": 10}
    assert body["status"]["carrying"] == {}


def test_deliver_items_away_from_dock_raises() -> None:
    _call("move_robot", {"x": 3, "y": 5})
    _call("fetch_item", {"sku": "SKU-1001", "qty": 5})
    with pytest.raises(ToolError):
        _call("deliver_items", {})


def test_reset_robot_tool() -> None:
    _call("move_robot", {"x": 3, "y": 5})
    _call("fetch_item", {"sku": "SKU-1001", "qty": 10})
    body = _call("reset_robot", {})
    assert body["status"] == "ok"

    status = _call("get_robot_status", {})
    assert (status["x"], status["y"]) == (0, 0)
    assert status["carrying"] == {}
