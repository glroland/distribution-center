from mcp.server import MCPServer

from .store import HelpRequest, SupervisorStore


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


def build_mcp_server(store: SupervisorStore) -> MCPServer:
    """Build an MCP server letting an AI agent escalate to a human supervisor."""

    mcp_server = MCPServer(
        name="supervisor-api",
        instructions=(
            "Tools for an AI agent to ask a human supervisor for help when it gets "
            "stuck. Call request_help with a clear question and any relevant "
            "context; the request is queued as 'open' for a supervisor to review "
            "and resolve through the REST API."
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

    return mcp_server
