from mcp.server.fastmcp import FastMCP as MCPServer

from .prompts import get_prompt
from .settings import settings
from .store import HelpRequest, SupervisorStore, TransferRequest
from .tracing import configure_tracing, tool_trace

configure_tracing()


def _request_dict(request: HelpRequest) -> dict:
    return {
        "id": request.id,
        "agent_id": request.agent_id,
        "question": request.question,
        "context": request.context,
        "status": request.status,
        "created_at": request.created_at.isoformat(),
        "resolved_at": request.resolved_at.isoformat() if request.resolved_at else None,
        "resolution": request.resolution,
    }


def _transfer_dict(request: TransferRequest) -> dict:
    return {
        "id": request.id,
        "agent_id": request.agent_id,
        "sku": request.sku,
        "quantity": request.quantity,
        "context": request.context,
        "status": request.status,
        "source_location": request.source_location,
        "created_at": request.created_at.isoformat(),
    }


def build_mcp_server(store: SupervisorStore) -> MCPServer:
    """Build an MCP server letting an AI agent escalate to a human supervisor."""

    instructions = get_prompt("supervisor-api.mcp_server.instructions").format()
    mcp_server = MCPServer(
        name="supervisor-api",
        instructions=instructions,
        host=settings.HOST,
        streamable_http_path="/",
    )

    @mcp_server.tool()
    @tool_trace
    def request_help(question: str, agent_id: str | None = None, context: str | None = None) -> dict:
        """Ask a human supervisor for help when stuck. `question` should clearly
        state what's blocking progress; `context` can include any extra detail,
        such as what's already been tried. Returns the queued help request,
        initially with status 'open'."""
        request = store.create_help_request(question, agent_id=agent_id, context=context)
        return _request_dict(request)

    @mcp_server.tool()
    @tool_trace
    def request_transfer(
        sku: str, quantity: int, agent_id: str | None = None, context: str | None = None
    ) -> dict:
        """Request an inventory transfer of `quantity` units of `sku` from
        another distribution center to cover a local shortfall. Resolves
        immediately: most of the time it succeeds (status 'available') and
        names the `source_location` it would ship from; the rest of the time
        the SKU turns out to be unavailable everywhere else too (status
        'unavailable', source_location null), at which point the agent should
        escalate with request_help instead."""
        request = store.create_transfer_request(
            sku, quantity, agent_id=agent_id, context=context
        )
        return _transfer_dict(request)

    return mcp_server
