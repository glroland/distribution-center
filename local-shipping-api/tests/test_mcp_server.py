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


def test_ship_order_tool() -> None:
    body = _call(
        "ship_order",
        {
            "po_number": "PO-1001",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St, Springfield",
            "items": [{"sku": "SKU-1001", "qty": 10}],
        },
    )
    assert body["po_number"] == "PO-1001"
    assert body["items"] == [{"sku": "SKU-1001", "qty": 10}]
    assert body["status"] == "shipped"
    assert body["carrier"] in {"UPS", "FedEx", "USPS", "DHL"}
    assert body["tracking_number"]


def test_ship_order_tool_queues_in_store() -> None:
    _call(
        "ship_order",
        {
            "po_number": "PO-1001",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St",
            "items": [{"sku": "SKU-1001", "qty": 10}],
        },
    )
    assert len(store.list_shipments()) == 1


def test_ship_order_tool_rejects_blank_po_number() -> None:
    with pytest.raises(ToolError):
        _call(
            "ship_order",
            {
                "po_number": "  ",
                "customer_name": "Jane Doe",
                "customer_address": "123 Main St",
                "items": [{"sku": "SKU-1001", "qty": 10}],
            },
        )


def test_ship_order_tool_rejects_malformed_items() -> None:
    with pytest.raises(ToolError):
        _call(
            "ship_order",
            {
                "po_number": "PO-1001",
                "customer_name": "Jane Doe",
                "customer_address": "123 Main St",
                "items": [{"sku": "SKU-1001"}],
            },
        )


def test_get_shipment_tool() -> None:
    created = _call(
        "ship_order",
        {
            "po_number": "PO-1001",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St",
            "items": [{"sku": "SKU-1001", "qty": 10}],
        },
    )
    body = _call("get_shipment", {"shipment_id": created["id"]})
    assert body["id"] == created["id"]


def test_get_shipment_tool_unknown_id_raises() -> None:
    with pytest.raises(ToolError):
        _call("get_shipment", {"shipment_id": 999})


def test_track_shipment_tool() -> None:
    created = _call(
        "ship_order",
        {
            "po_number": "PO-1001",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St",
            "items": [{"sku": "SKU-1001", "qty": 10}],
        },
    )
    body = _call("track_shipment", {"tracking_number": created["tracking_number"]})
    assert body["id"] == created["id"]


def test_track_shipment_tool_unknown_raises() -> None:
    with pytest.raises(ToolError):
        _call("track_shipment", {"tracking_number": "does-not-exist"})


def test_list_shipments_tool() -> None:
    _call(
        "ship_order",
        {
            "po_number": "PO-1001",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St",
            "items": [{"sku": "SKU-1001", "qty": 10}],
        },
    )
    body = _call("list_shipments", {})
    assert len(body["shipments"]) == 1


def test_list_shipments_tool_filters_by_po_number() -> None:
    _call(
        "ship_order",
        {
            "po_number": "PO-1001",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St",
            "items": [{"sku": "SKU-1001", "qty": 10}],
        },
    )
    _call(
        "ship_order",
        {
            "po_number": "PO-1002",
            "customer_name": "John Doe",
            "customer_address": "456 Elm St",
            "items": [{"sku": "SKU-1001", "qty": 5}],
        },
    )
    body = _call("list_shipments", {"po_number": "PO-1001"})
    assert len(body["shipments"]) == 1
    assert body["shipments"][0]["po_number"] == "PO-1001"


def test_reset_shipments_tool() -> None:
    _call(
        "ship_order",
        {
            "po_number": "PO-1001",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St",
            "items": [{"sku": "SKU-1001", "qty": 10}],
        },
    )
    body = _call("reset_shipments", {})
    assert body["status"] == "ok"
    assert store.list_shipments() == []
