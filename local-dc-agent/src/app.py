from contextlib import asynccontextmanager

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

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
)

app = A2AStarletteApplication(agent_card=AGENT_CARD, http_handler=request_handler).build(lifespan=lifespan)


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


app.add_route("/health", health, methods=["GET"])
app.add_route("/ready", ready, methods=["GET"])
app.add_route("/guardrails", get_guardrails, methods=["GET"])
app.add_route("/guardrails", set_guardrails, methods=["POST"])
