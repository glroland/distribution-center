from contextlib import asynccontextmanager

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.utils.constants import DEFAULT_RPC_URL
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import guardrails
from .agent_card import AGENT_CARD
from .agent_executor import ProcessOrderAgentExecutor
from .tracing import configure_tracing
from .worker import OrderWorker

configure_tracing()
worker = OrderWorker()


@asynccontextmanager
async def lifespan(app: Starlette):
    await worker.start()
    try:
        yield
    finally:
        await worker.stop()


request_handler = DefaultRequestHandler(
    agent_executor=ProcessOrderAgentExecutor(worker),
    task_store=InMemoryTaskStore(),
    agent_card=AGENT_CARD,
)


async def health(request: Request) -> JSONResponse:
    """Liveness: the process is up and serving. Deliberately independent of
    MCP connectivity -- see /ready -- so a slow/unavailable downstream server
    doesn't cause Kubernetes to kill and restart a pod that's correctly
    waiting/retrying rather than wedged."""
    return JSONResponse({"status": "ok"})


async def ready(request: Request) -> JSONResponse:
    """Readiness: reflects whether the worker has finished connecting to all
    downstream MCP servers (wms/robot/shipping/supervisor/label). Not ready
    yet is expected and normal right after a fresh deploy or cluster restart,
    while McpToolRouter retries those connections in the background."""
    if worker.is_ready():
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "starting"}, status_code=503)


async def get_guardrails(request: Request) -> JSONResponse:
    """"Agentic Safety" toggle state -- see guardrails.py for what it gates
    (the pre-fulfillment injection scan, the adjust_inventory bound check,
    tool-result redaction, and hiding the destructive reset_* tools)."""
    return JSONResponse({"enabled": guardrails.is_enabled()})


async def set_guardrails(request: Request) -> JSONResponse:
    body = await request.json()
    enabled = bool(body.get("enabled"))
    guardrails.set_enabled(enabled)
    return JSONResponse({"enabled": enabled})


app = Starlette(
    routes=[
        *create_agent_card_routes(AGENT_CARD),
        # A2A 1.0 JSON-RPC (SendMessage, GetTask, ...). v0.3 compat keeps the
        # same endpoint answering pre-1.0 callers (message/send, `kind`-tagged
        # parts) so an external client that hasn't upgraded isn't cut off.
        *create_jsonrpc_routes(request_handler, rpc_url=DEFAULT_RPC_URL, enable_v0_3_compat=True),
        Route("/health", health, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
        Route("/guardrails", get_guardrails, methods=["GET"]),
        Route("/guardrails", set_guardrails, methods=["POST"]),
    ],
    lifespan=lifespan,
)
