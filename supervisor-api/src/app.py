from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException

from .mcp_registration import configure_mcp_registration
from .mcp_server import build_mcp_server
from .models import HelpRequestResponse, ResolveRequest, TransferRequestResponse
from .settings import settings
from .store import (
    HelpRequest,
    HelpRequestAlreadyResolvedError,
    HelpRequestNotFoundError,
    SupervisorStore,
    TransferRequest,
    TransferRequestNotFoundError,
)
from .tracing import configure_tracing

configure_tracing()
store = SupervisorStore(unavailable_chance=settings.TRANSFER_UNAVAILABLE_CHANCE)
mcp_server = build_mcp_server(store)
mcp_app = mcp_server.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
        await configure_mcp_registration()
        yield


app = FastAPI(title="Local Supervisor API", lifespan=lifespan)
app.mount("/mcp", mcp_app)


def _to_response(request: HelpRequest) -> HelpRequestResponse:
    return HelpRequestResponse(
        id=request.id,
        agent_id=request.agent_id,
        question=request.question,
        context=request.context,
        status=request.status,
        created_at=request.created_at,
        resolved_at=request.resolved_at,
        resolution=request.resolution,
    )


def _to_transfer_response(request: TransferRequest) -> TransferRequestResponse:
    return TransferRequestResponse(
        id=request.id,
        agent_id=request.agent_id,
        sku=request.sku,
        quantity=request.quantity,
        context=request.context,
        status=request.status,
        source_location=request.source_location,
        created_at=request.created_at,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/help-requests", response_model=list[HelpRequestResponse])
def list_help_requests(status: str | None = None) -> list[HelpRequestResponse]:
    if status is not None and status not in ("open", "resolved"):
        raise HTTPException(status_code=400, detail="status must be 'open' or 'resolved'")
    return [_to_response(r) for r in store.list_help_requests(status)]


@app.post("/help-requests/reset")
def reset_help_requests() -> dict[str, str]:
    store.reset()
    return {"status": "ok"}


@app.get("/help-requests/{request_id}", response_model=HelpRequestResponse)
def get_help_request(request_id: int) -> HelpRequestResponse:
    try:
        request = store.get_help_request(request_id)
    except HelpRequestNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown help request: {request_id}"
        ) from None
    return _to_response(request)


@app.post("/help-requests/{request_id}/resolve", response_model=HelpRequestResponse)
def resolve_help_request(request_id: int, body: ResolveRequest) -> HelpRequestResponse:
    try:
        request = store.resolve_help_request(request_id, body.resolution)
    except HelpRequestNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown help request: {request_id}"
        ) from None
    except HelpRequestAlreadyResolvedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_response(request)


@app.get("/transfer-requests", response_model=list[TransferRequestResponse])
def list_transfer_requests(status: str | None = None) -> list[TransferRequestResponse]:
    if status is not None and status not in ("available", "unavailable"):
        raise HTTPException(status_code=400, detail="status must be 'available' or 'unavailable'")
    return [_to_transfer_response(r) for r in store.list_transfer_requests(status)]


@app.get("/transfer-requests/{request_id}", response_model=TransferRequestResponse)
def get_transfer_request(request_id: int) -> TransferRequestResponse:
    try:
        request = store.get_transfer_request(request_id)
    except TransferRequestNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Unknown transfer request: {request_id}"
        ) from None
    return _to_transfer_response(request)
