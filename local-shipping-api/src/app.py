from fastapi import FastAPI, HTTPException

from .mcp_server import build_mcp_server
from .models import CreateShipmentRequest, ResetResponse, ShipmentItemResponse, ShipmentResponse
from .shipping import (
    InvalidShipmentError,
    Shipment,
    ShipmentNotFoundError,
    ShippingStore,
    TrackingNumberNotFoundError,
)
from .settings import settings
from .tracing import configure_tracing

configure_tracing()
store = ShippingStore()
mcp_server = build_mcp_server(store)
mcp_app = mcp_server.http_app(path="/")

app = FastAPI(title="Local Shipping API", lifespan=mcp_app.lifespan)
app.mount("/mcp", mcp_app)


def _to_response(shipment: Shipment) -> ShipmentResponse:
    return ShipmentResponse(
        id=shipment.id,
        po_number=shipment.po_number,
        customer_name=shipment.customer_name,
        customer_address=shipment.customer_address,
        items=[ShipmentItemResponse(sku=item.sku, qty=item.qty) for item in shipment.items],
        carrier=shipment.carrier,
        tracking_number=shipment.tracking_number,
        status=shipment.status,
        shipped_at=shipment.shipped_at,
        estimated_delivery=shipment.estimated_delivery,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/shipments", response_model=ShipmentResponse)
def create_shipment(body: CreateShipmentRequest) -> ShipmentResponse:
    try:
        shipment = store.create_shipment(
            body.po_number,
            body.customer_name,
            body.customer_address,
            [(item.sku, item.qty) for item in body.items],
        )
    except InvalidShipmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_response(shipment)


@app.get("/shipments", response_model=list[ShipmentResponse])
def list_shipments(po_number: str | None = None) -> list[ShipmentResponse]:
    return [_to_response(s) for s in store.list_shipments(po_number)]


@app.get("/shipments/tracking/{tracking_number}", response_model=ShipmentResponse)
def get_shipment_by_tracking(tracking_number: str) -> ShipmentResponse:
    try:
        shipment = store.get_shipment_by_tracking(tracking_number)
    except TrackingNumberNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown tracking number: {tracking_number}"
        ) from None
    return _to_response(shipment)


@app.get("/shipments/{shipment_id}", response_model=ShipmentResponse)
def get_shipment(shipment_id: int) -> ShipmentResponse:
    try:
        shipment = store.get_shipment(shipment_id)
    except ShipmentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown shipment: {shipment_id}") from None
    return _to_response(shipment)


@app.post("/shipments/reset", response_model=ResetResponse)
def reset_shipments() -> ResetResponse:
    store.reset()
    return ResetResponse(status="ok", shipment_count=len(store.list_shipments()))
