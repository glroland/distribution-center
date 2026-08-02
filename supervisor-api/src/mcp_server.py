from mcp.server import MCPServer

from .store import HelpRequest, SupervisorStore, TransferRequest


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

    mcp_server = MCPServer(
        name="supervisor-api",
        instructions=(
            "Tools for an AI agent to ask a human supervisor for help when it gets "
            "stuck. Call request_help with a clear question and any relevant "
            "context; the request is queued as 'open' for a supervisor to review "
            "and resolve through the REST API. Call request_transfer before "
            "escalating a stock shortfall to a human - it checks whether another "
            "distribution center can cover the shortfall and, if so, returns "
            "immediately with status 'available' and a source_location; there is "
            "a chance (configurable, default 1 in 3) that the SKU is unavailable "
            "everywhere else too, in which case status is 'unavailable' and the "
            "agent should fall back to request_help."
        ),
    )

    @mcp_server.tool()
    def request_help(question: str, agent_id: str | None = None, context: str | None = None) -> dict:
        """Ask a human supervisor for help when stuck. `question` should clearly
        state what's blocking progress; `context` can include any extra detail,
        such as what's already been tried. Returns the queued help request,
        initially with status 'open'."""
        request = store.create_help_request(question, agent_id=agent_id, context=context)
        return _request_dict(request)

    @mcp_server.tool()
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
