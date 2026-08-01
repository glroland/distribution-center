from contextlib import asynccontextmanager

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

from .agent_card import AGENT_CARD
from .agent_executor import ProcessOrderAgentExecutor
from .worker import OrderWorker

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
