import json
import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .models import DistributionCenter
from .po_catalog import PurchaseOrderNotFoundError, list_purchase_orders, resolve_purchase_order_path
from .run_manager import run_manager, stream_events
from .settings import DC_BY_NAME, DISTRIBUTION_CENTERS, settings
from .warehouse_map import scan_shelf_grid

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_BOOST_TARGET_QTY = 1_000_000

app = FastAPI(title="DC Demo Dashboard")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


def _get_dc(name: str) -> DistributionCenter:
    dc = DC_BY_NAME.get(name)
    if dc is None:
        raise HTTPException(status_code=404, detail=f"Unknown distribution center: {name}")
    return dc


async def _proxy_get(url: str, params: dict | None = None) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        logger.warning("Could not reach %s: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"Could not reach {url}: {exc}") from None
    return JSONResponse(content=response.json(), status_code=response.status_code)


async def _proxy_post(url: str, json_body: dict | None = None) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=json_body)
    except httpx.HTTPError as exc:
        logger.warning("Could not reach %s: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"Could not reach {url}: {exc}") from None
    return JSONResponse(content=response.json(), status_code=response.status_code)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/api/dcs")
async def get_distribution_centers() -> list[dict]:
    centers = []
    for dc in DISTRIBUTION_CENTERS:
        location_name = dc.display_name
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{dc.wms_url}/location")
                if response.status_code == 200:
                    location_name = response.json().get("location_name", location_name)
        except httpx.HTTPError:
            pass
        centers.append({**dc.model_dump(), "location_name": location_name})
    return centers


@app.get("/api/dcs/{name}/pos")
def get_purchase_orders(name: str) -> list[dict]:
    _get_dc(name)
    return [po.model_dump() for po in list_purchase_orders()]


@app.get("/api/pos/{filename}/file")
def get_purchase_order_file(filename: str) -> FileResponse:
    try:
        path = resolve_purchase_order_path(filename)
    except PurchaseOrderNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown PO file: {filename}") from None
    return FileResponse(path, media_type="application/pdf")


@app.get("/api/dcs/{name}/map")
async def get_warehouse_map(name: str) -> dict:
    dc = _get_dc(name)
    return await scan_shelf_grid(dc)


@app.get("/api/dcs/{name}/inventory")
async def get_inventory(name: str) -> JSONResponse:
    dc = _get_dc(name)
    return await _proxy_get(f"{dc.wms_url}/inventory")


@app.get("/api/dcs/{name}/shipments")
async def get_shipments(name: str, po_number: str | None = None) -> JSONResponse:
    dc = _get_dc(name)
    params = {"po_number": po_number} if po_number else None
    return await _proxy_get(f"{dc.shipping_url}/shipments", params=params)


@app.post("/api/dcs/{name}/reset")
async def reset_dc(name: str) -> dict:
    dc = _get_dc(name)
    results = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for label, url in (
            ("inventory", f"{dc.wms_url}/inventory/reset"),
            ("robot", f"{dc.robot_url}/reset"),
            ("shipments", f"{dc.shipping_url}/shipments/reset"),
            ("help_requests", f"{settings.SUPERVISOR_API_URL}/help-requests/reset"),
        ):
            try:
                response = await client.post(url)
                results[label] = response.status_code == 200
            except httpx.HTTPError as exc:
                results[label] = False
                results[f"{label}_error"] = str(exc)
    return results


@app.post("/api/dcs/{name}/inventory-boost")
async def set_inventory_boost(name: str, request: Request) -> dict:
    dc = _get_dc(name)
    body = await request.json()
    enabled = bool(body.get("enabled"))

    if enabled:
        calls = (
            ("inventory", f"{dc.wms_url}/inventory/boost", {"target_qty": _BOOST_TARGET_QTY}),
            ("shelves", f"{dc.robot_url}/shelves/boost", {"target_qty": _BOOST_TARGET_QTY}),
        )
    else:
        calls = (
            ("inventory", f"{dc.wms_url}/inventory/reset", None),
            ("shelves", f"{dc.robot_url}/reset", None),
        )

    results: dict = {"enabled": enabled}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for label, url, json_body in calls:
            try:
                response = await client.post(url, json=json_body)
                results[label] = response.status_code == 200
            except httpx.HTTPError as exc:
                results[label] = False
                results[f"{label}_error"] = str(exc)
    return results


@app.get("/api/stickers/{sku}")
async def get_sticker_photo(sku: str, color_mode: str = "random", image_format: str = "jpg") -> Response:
    url = f"{settings.LABEL_API_URL}/stickers/{sku}"
    params = {"color_mode": color_mode, "image_format": image_format}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        logger.warning("Could not reach %s: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"Could not reach {url}: {exc}") from None
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/octet-stream"),
    )


@app.get("/api/help-requests")
async def get_help_requests(status: str | None = None) -> JSONResponse:
    params = {"status": status} if status else None
    return await _proxy_get(f"{settings.SUPERVISOR_API_URL}/help-requests", params=params)


@app.post("/api/help-requests/{request_id}/resolve")
async def resolve_help_request(request_id: int, request: Request) -> JSONResponse:
    body = await request.json()
    return await _proxy_post(f"{settings.SUPERVISOR_API_URL}/help-requests/{request_id}/resolve", json_body=body)


@app.post("/api/runs")
async def create_run(request: Request) -> dict:
    body = await request.json()
    dc_name = body.get("dc")
    filename = body.get("filename")
    if not dc_name or not filename:
        raise HTTPException(status_code=400, detail="Both 'dc' and 'filename' are required")

    dc = _get_dc(dc_name)
    try:
        resolve_purchase_order_path(filename)
    except PurchaseOrderNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown PO file: {filename}") from None

    run = run_manager.start_run(dc, filename)
    logger.info("Run %s created for dc=%s, po=%s", run.id, dc_name, filename)
    return {"run_id": run.id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = run_manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run")
    return {"run_id": run.id, "dc": run.dc_name, "po_filename": run.po_filename, "done": run.done}


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    run = run_manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run")

    async def event_source():
        async for event in stream_events(run):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.post("/api/internal/events/{run_id}")
async def receive_progress_event(run_id: str, request: Request) -> Response:
    body = await request.json()
    await run_manager.ingest_webhook_event(run_id, body.get("type", "unknown"), body.get("data", {}))
    return Response(status_code=204)
