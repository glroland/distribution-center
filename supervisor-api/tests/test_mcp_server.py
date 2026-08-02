import asyncio
import json

import pytest
from fastmcp.exceptions import ToolError

from src.app import mcp_server, store


@pytest.fixture(autouse=True)
def _reset_store():
    original_unavailable_chance = store._unavailable_chance
    store.reset()
    yield
    store.reset()
    store._unavailable_chance = original_unavailable_chance


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


def test_request_transfer_tool_available() -> None:
    store._unavailable_chance = 0.0
    body = _call(
        "request_transfer",
        {"sku": "SKU-1", "quantity": 5, "agent_id": "agent-1", "context": "short 5 units"},
    )
    assert body["id"] == 1
    assert body["sku"] == "SKU-1"
    assert body["quantity"] == 5
    assert body["agent_id"] == "agent-1"
    assert body["context"] == "short 5 units"
    assert body["status"] == "available"
    assert body["source_location"] is not None


def test_request_transfer_tool_unavailable() -> None:
    store._unavailable_chance = 1.0
    body = _call("request_transfer", {"sku": "SKU-1", "quantity": 5})
    assert body["status"] == "unavailable"
    assert body["source_location"] is None


def test_request_transfer_tool_defaults() -> None:
    body = _call("request_transfer", {"sku": "SKU-1", "quantity": 1})
    assert body["agent_id"] is None
    assert body["context"] is None


def test_request_transfer_tool_queues_in_store() -> None:
    _call("request_transfer", {"sku": "SKU-1", "quantity": 1})
    assert len(store.list_transfer_requests()) == 1


def test_request_transfer_tool_blank_sku_raises() -> None:
    with pytest.raises(ToolError):
        _call("request_transfer", {"sku": "   ", "quantity": 1})


def test_request_transfer_tool_nonpositive_quantity_raises() -> None:
    with pytest.raises(ToolError):
        _call("request_transfer", {"sku": "SKU-1", "quantity": 0})
