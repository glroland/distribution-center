from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException

from .inventory import InsufficientQuantityError, InventoryStore, SkuNotFoundError
from .mcp_server import build_mcp_server
from .models import InventoryItemResponse, LocationResponse, QuantityRequest, ResetResponse
from .settings import settings
from .tracing import configure_tracing

configure_tracing()
store = InventoryStore(settings.inventory_csv_path(), settings.LOCATION_NAME)
mcp_server = build_mcp_server(store)
mcp_app = mcp_server.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
        yield


app = FastAPI(title="Local WMS API", lifespan=lifespan)
app.mount("/mcp", mcp_app)


def _to_response(item) -> InventoryItemResponse:
    return InventoryItemResponse(
        sku=item.sku,
        on_hand_qty=item.on_hand_qty,
        location_x=item.location_x,
        location_y=item.location_y,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/location", response_model=LocationResponse)
def get_location() -> LocationResponse:
    return LocationResponse(location_name=store.get_location_name())


@app.get("/inventory", response_model=list[InventoryItemResponse])
def list_inventory() -> list[InventoryItemResponse]:
    return [_to_response(item) for item in store.list_items()]


@app.get("/inventory/{sku}", response_model=InventoryItemResponse)
def get_inventory(sku: str) -> InventoryItemResponse:
    try:
        item = store.get_item(sku)
    except SkuNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown SKU: {sku}") from None
    return _to_response(item)


@app.post("/inventory/{sku}/increment", response_model=InventoryItemResponse)
def increment_inventory(sku: str, body: QuantityRequest) -> InventoryItemResponse:
    try:
        item = store.increment(sku, body.qty)
    except SkuNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown SKU: {sku}") from None
    return _to_response(item)


@app.post("/inventory/{sku}/decrement", response_model=InventoryItemResponse)
def decrement_inventory(sku: str, body: QuantityRequest) -> InventoryItemResponse:
    try:
        item = store.decrement(sku, body.qty)
    except SkuNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown SKU: {sku}") from None
    except InsufficientQuantityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_response(item)


@app.post("/inventory/reset", response_model=ResetResponse)
def reset_inventory() -> ResetResponse:
    store.reset()
    return ResetResponse(status="ok", item_count=len(store.list_items()))
