from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

from .agent_card import AGENT_CARD
from .agent_executor import ProcessOrderAgentExecutor

request_handler = DefaultRequestHandler(
    agent_executor=ProcessOrderAgentExecutor(),
    task_store=InMemoryTaskStore(),
)

app = A2AStarletteApplication(agent_card=AGENT_CARD, http_handler=request_handler).build()
