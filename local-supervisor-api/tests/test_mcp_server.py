import asyncio
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.app import mcp_server, store


@pytest.fixture(autouse=True)
def _reset_store():
    store.reset()
    yield
    store.reset()


def _call(tool: str, args: dict) -> dict:
    result = asyncio.run(mcp_server.call_tool(tool, args))
    text = "".join(part.text for part in result.content if hasattr(part, "text"))
    return json.loads(text)


def test_request_help_tool() -> None:
    body = _call(
        "request_help",
        {"question": "Which SKU should I pick first?", "agent_id": "agent-1", "context": "two open orders"},
    )
    assert body["id"] == 1
    assert body["question"] == "Which SKU should I pick first?"
    assert body["agent_id"] == "agent-1"
    assert body["context"] == "two open orders"
    assert body["status"] == "open"
    assert body["resolved_at"] is None


def test_request_help_tool_defaults() -> None:
    body = _call("request_help", {"question": "What now?"})
    assert body["agent_id"] is None
    assert body["context"] is None


def test_request_help_tool_queues_in_store() -> None:
    _call("request_help", {"question": "What now?"})
    assert len(store.list_help_requests()) == 1


def test_request_help_tool_blank_question_raises() -> None:
    with pytest.raises(ToolError):
        _call("request_help", {"question": "   "})
