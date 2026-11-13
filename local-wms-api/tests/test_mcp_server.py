import asyncio
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.app import mcp_server, store


@pytest.fixture(autouse=True)
def _reset_store():
    store.reset()
    yield
    store.reset()


def _call(tool: str, args: dict) -> dict:
    result = asyncio.run(mcp_server.call_tool(tool, args))
    text = "".join(part.text for part in result.content if hasattr(part, "text"))
    return json.loads(text)


def test_get_location_tool() -> None:
    body = _call("get_location", {})
    assert body == {"location_name": store.get_location_name()}


def test_get_inventory_status_single_sku() -> None:
    body = _call("get_inventory_status", {"sku": "SKU-1001"})
    assert body["on_hand_qty"] == 60


def test_get_inventory_status_all_skus() -> None:
    body = _call("get_inventory_status", {})
    assert len(body["items"]) == 18


def test_get_inventory_status_unknown_sku_raises() -> None:
    with pytest.raises(ToolError):
        _call("get_inventory_status", {"sku": "does-not-exist"})


def test_adjust_inventory_positive_delta_receives_stock() -> None:
    body = _call("adjust_inventory", {"sku": "SKU-1001", "delta": 10})
    assert body["on_hand_qty"] == 70


def test_adjust_inventory_negative_delta_ships_stock() -> None:
    body = _call("adjust_inventory", {"sku": "SKU-1001", "delta": -20})
    assert body["on_hand_qty"] == 40


def test_adjust_inventory_overship_raises() -> None:
    with pytest.raises(ToolError):
        _call("adjust_inventory", {"sku": "SKU-1002", "delta": -10000})


def test_reset_inventory_tool() -> None:
    _call("adjust_inventory", {"sku": "SKU-1001", "delta": 500})
    body = _call("reset_inventory", {})
    assert body["status"] == "ok"

    status = _call("get_inventory_status", {"sku": "SKU-1001"})
    assert status["on_hand_qty"] == 60
