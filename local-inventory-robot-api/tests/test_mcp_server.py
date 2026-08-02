import asyncio
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.app import mcp_server, robot


@pytest.fixture(autouse=True)
def _reset_robot():
    robot.reset()
    robot._move_step_delay = 0
    yield
    robot.reset()


def _call(tool: str, args: dict) -> dict:
    result = asyncio.run(mcp_server.call_tool(tool, args))
    text = "".join(part.text for part in result.content if hasattr(part, "text"))
    return json.loads(text)


def _goto(x: int, y: int) -> dict:
    """Walk the robot to (x, y) one grid cell at a time via move_robot, so the
    route never crosses (only ever lands on) a cell holding product."""
    status = _call("get_robot_status", {})
    cx, cy = status["x"], status["y"]
    while (cx, cy) != (x, y):
        if cx != x:
            cx += 1 if x > cx else -1
        else:
            cy += 1 if y > cy else -1
        status = _call("move_robot", {"x": cx, "y": cy})
    return status


def test_get_robot_status_tool() -> None:
    body = _call("get_robot_status", {})
    assert body == {"x": 0, "y": 0, "carrying": {}, "capacity": 100, "carrying_total": 0}


def test_find_item_tool() -> None:
    body = _call("find_item", {"sku": "SKU-1001"})
    locations = {(loc["location_x"], loc["location_y"]): loc["qty"] for loc in body["locations"]}
    assert locations == {(1, 1): 50, (5, 9): 10}


def test_find_item_tool_unknown_sku_returns_empty() -> None:
    body = _call("find_item", {"sku": "does-not-exist"})
    assert body["locations"] == []


def test_get_shelf_inventory_at_location() -> None:
    body = _call("get_shelf_inventory", {"x": 3, "y": 1})
    assert body == {"location_x": 3, "location_y": 1, "stock": {"SKU-1002": 20}}


def test_get_shelf_inventory_at_current_location() -> None:
    _goto(5, 1)
    body = _call("get_shelf_inventory", {})
    assert body == {"location_x": 5, "location_y": 1, "stock": {"SKU-1003": 15}}


def test_move_robot_tool() -> None:
    # x = 9 is never stocked, so this is a plain, unobstructed move.
    body = _call("move_robot", {"x": 9, "y": 3})
    assert (body["x"], body["y"]) == (9, 3)


def test_move_robot_out_of_bounds_raises() -> None:
    with pytest.raises(ToolError):
        _call("move_robot", {"x": 99, "y": 0})


def test_move_robot_blocked_by_product_raises() -> None:
    # the straight path from the dock to (1, 5) crosses (1, 1), which stocks
    # SKU-1001 - the move should be rejected rather than driving through it
    with pytest.raises(ToolError):
        _call("move_robot", {"x": 1, "y": 5})
    status = _call("get_robot_status", {})
    assert (status["x"], status["y"]) == (0, 0)


def test_fetch_item_tool() -> None:
    _goto(1, 1)
    body = _call("fetch_item", {"sku": "SKU-1001", "qty": 10})
    assert body["carrying"] == {"SKU-1001": 10}


def test_fetch_item_unknown_sku_raises() -> None:
    _goto(1, 1)
    with pytest.raises(ToolError):
        _call("fetch_item", {"sku": "does-not-exist", "qty": 1})


def test_deliver_items_round_trip() -> None:
    _goto(1, 1)
    _call("fetch_item", {"sku": "SKU-1001", "qty": 10})
    _goto(0, 0)
    body = _call("deliver_items", {})
    assert body["delivered"] == {"SKU-1001": 10}
    assert body["status"]["carrying"] == {}


def test_deliver_items_away_from_dock_raises() -> None:
    _goto(1, 1)
    _call("fetch_item", {"sku": "SKU-1001", "qty": 5})
    with pytest.raises(ToolError):
        _call("deliver_items", {})


def test_restock_shelf_tool_explicit_location() -> None:
    body = _call("restock_shelf", {"sku": "SKU-9999", "qty": 12, "x": 4, "y": 4})
    assert body == {"location_x": 4, "location_y": 4, "stock": {"SKU-9999": 12}}


def test_restock_shelf_tool_auto_placement_prefers_existing_sku_location() -> None:
    body = _call("restock_shelf", {"sku": "SKU-1002", "qty": 5})
    assert body == {"location_x": 3, "location_y": 1, "stock": {"SKU-1002": 25}}


def test_restock_shelf_tool_then_fetchable() -> None:
    _call("restock_shelf", {"sku": "SKU-9999", "qty": 8, "x": 4, "y": 4})
    _goto(4, 4)
    body = _call("fetch_item", {"sku": "SKU-9999", "qty": 8})
    assert body["carrying"] == {"SKU-9999": 8}


def test_restock_shelf_tool_at_dock_raises() -> None:
    with pytest.raises(ToolError):
        _call("restock_shelf", {"sku": "SKU-9999", "qty": 1, "x": 0, "y": 0})


def test_restock_shelf_tool_nonpositive_qty_raises() -> None:
    with pytest.raises(ToolError):
        _call("restock_shelf", {"sku": "SKU-9999", "qty": 0, "x": 4, "y": 4})


def test_reset_robot_tool() -> None:
    _goto(1, 1)
    _call("fetch_item", {"sku": "SKU-1001", "qty": 10})
    body = _call("reset_robot", {})
    assert body["status"] == "ok"

    status = _call("get_robot_status", {})
    assert (status["x"], status["y"]) == (0, 0)
    assert status["carrying"] == {}
