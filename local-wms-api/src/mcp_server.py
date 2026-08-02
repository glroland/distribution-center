from mcp.server import MCPServer

from .inventory import InventoryItem, InventoryStore
from .tracing import configure_tracing, tool_trace

configure_tracing()


def _item_dict(item: InventoryItem) -> dict:
    return {
        "sku": item.sku,
        "on_hand_qty": item.on_hand_qty,
        "location_x": item.location_x,
        "location_y": item.location_y,
    }


def build_mcp_server(store: InventoryStore) -> MCPServer:
    """Build a coarse-grained MCP server for LLM-driven inventory management."""

    mcp_server = MCPServer(
        name="local-wms-api",
        instructions=(
            "Tools for managing inventory at a single virtual warehouse location. "
            "Use get_location to learn the location's name, get_inventory_status to "
            "look up on-hand quantity and bin coordinates for one SKU or every SKU, "
            "adjust_inventory to receive or ship stock for a SKU, and reset_inventory "
            "to restore the demo data to its starting state."
        ),
    )

    @mcp_server.tool()
    @tool_trace
    def get_location() -> dict:
        """Get the name of the virtual warehouse location managed by this WMS."""
        return {"location_name": store.get_location_name()}

    @mcp_server.tool()
    @tool_trace
    def get_inventory_status(sku: str | None = None) -> dict:
        """Get on-hand quantity and bin location for one SKU, or every SKU if sku is omitted."""
        if sku is None:
            return {"items": [_item_dict(item) for item in store.list_items()]}
        return _item_dict(store.get_item(sku))

    @mcp_server.tool()
    @tool_trace
    def adjust_inventory(sku: str, delta: int) -> dict:
        """Adjust on-hand quantity for a SKU. A positive delta receives stock, a
        negative delta ships stock. Fails if shipping would take quantity below zero."""
        if delta > 0:
            item = store.increment(sku, delta)
        elif delta < 0:
            item = store.decrement(sku, -delta)
        else:
            item = store.get_item(sku)
        return _item_dict(item)

    @mcp_server.tool()
    @tool_trace
    def reset_inventory() -> dict:
        """Reset inventory to the original demo data loaded from the CSV file."""
        store.reset()
        return {"status": "ok", "item_count": len(store.list_items())}

    return mcp_server
