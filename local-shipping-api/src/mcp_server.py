from mcp.server import MCPServer

from .shipping import Shipment, ShippingStore


def _shipment_dict(shipment: Shipment) -> dict:
    return {
        "id": shipment.id,
        "po_number": shipment.po_number,
        "customer_name": shipment.customer_name,
        "customer_address": shipment.customer_address,
        "items": [{"sku": item.sku, "qty": item.qty} for item in shipment.items],
        "carrier": shipment.carrier,
        "tracking_number": shipment.tracking_number,
        "status": shipment.status,
        "shipped_at": shipment.shipped_at.isoformat(),
        "estimated_delivery": shipment.estimated_delivery.isoformat(),
    }


def build_mcp_server(store: ShippingStore) -> MCPServer:
    """Build an MCP server for an LLM to ship items gathered for a purchase order."""

    mcp_server = MCPServer(
        name="local-shipping-api",
        instructions=(
            "Tools for shipping product a warehouse robot has gathered to fulfil a "
            "purchase order. Once every item for a PO has been picked and "
            "delivered to the dock, call ship_order with the PO number, the "
            "customer's name and address, and the list of SKUs/quantities being "
            "shipped. This mocks handing the package to a carrier: it randomly "
            "assigns a carrier, generates a carrier-formatted tracking number, "
            "and estimates a delivery date - no real carrier is contacted. Use "
            "track_shipment or get_shipment afterward to look up a shipment's "
            "tracking details."
        ),
    )

    @mcp_server.tool()
    def ship_order(
        po_number: str, customer_name: str, customer_address: str, items: list[dict]
    ) -> dict:
        """Ship the given items to a customer to fulfil a purchase order. `items`
        is a list of objects each with a `sku` and `qty` key. Returns the
        created shipment, including its carrier, tracking number, and estimated
        delivery date."""
        try:
            line_items = [(item["sku"], item["qty"]) for item in items]
        except (KeyError, TypeError) as exc:
            raise ValueError("each item must be an object with 'sku' and 'qty' keys") from exc
        shipment = store.create_shipment(po_number, customer_name, customer_address, line_items)
        return _shipment_dict(shipment)

    @mcp_server.tool()
    def get_shipment(shipment_id: int) -> dict:
        """Look up a shipment by its id."""
        return _shipment_dict(store.get_shipment(shipment_id))

    @mcp_server.tool()
    def track_shipment(tracking_number: str) -> dict:
        """Look up a shipment by its carrier tracking number."""
        return _shipment_dict(store.get_shipment_by_tracking(tracking_number))

    @mcp_server.tool()
    def list_shipments(po_number: str | None = None) -> dict:
        """List shipments, optionally filtered to a single PO number."""
        return {"shipments": [_shipment_dict(s) for s in store.list_shipments(po_number)]}

    @mcp_server.tool()
    def reset_shipments() -> dict:
        """Clear all shipments. Intended for demo/test reset."""
        store.reset()
        return {"status": "ok"}

    return mcp_server
