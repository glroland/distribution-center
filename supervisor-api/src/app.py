from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException

from .mcp_server import build_mcp_server
from .models import HelpRequestResponse, ResolveRequest
from .store import HelpRequest, HelpRequestAlreadyResolvedError, HelpRequestNotFoundError, SupervisorStore

store = SupervisorStore()
mcp_server = build_mcp_server(store)
mcp_app = mcp_server.streamable_http_app(streamable_http_path="/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
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
