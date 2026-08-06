import json
from dataclasses import dataclass, field

import pytest

from src import fulfillment as fulfillment_module
from src.fulfillment import FulfillmentError, fulfill_order
from src.mcp_tools import ToolCallError
from src.models import ExtractedOrder, LineItem
from src.order_processing import process_order
from src.prompts import get_prompt
from src.settings import settings


@dataclass
class _FakeFunctionCall:
    name: str
    arguments: str


@dataclass
class _FakeToolCall:
    id: str
    function: _FakeFunctionCall
    type: str = "function"


class _FakeMessage:
    def __init__(self, tool_calls=None, content=None):
        self.role = "assistant"
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, exclude_none=True) -> dict:
        data: dict = {"role": self.role}
        if self.content is not None:
            data["content"] = self.content
        if self.tool_calls:
            data["tool_calls"] = [
                {"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in self.tool_calls
            ]
        return data


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def __init__(self, responses: list[_FakeMessage]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    def create(self, **kwargs) -> _FakeResponse:
        self.call_count += 1
        if not self._responses:
            raise AssertionError("no more fake responses queued")
        return _FakeResponse(self._responses.pop(0))


class _FakeOpenAIClient:
    def __init__(self, responses: list[_FakeMessage]) -> None:
        self.chat = type("Chat", (), {"completions": _FakeCompletions(responses)})()


@dataclass
class _FakeTools:
    tool_results: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def list_openai_tools(self) -> list[dict]:
        return []

    def server_instructions(self) -> dict[str, str]:
        return {"wms": "manage inventory", "robot": "drive the robot"}

    async def call(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if name not in self.tool_results:
            raise ToolCallError(f"unexpected tool call {name}")
        result = self.tool_results[name]
        if isinstance(result, Exception):
            raise result
        return result


def _order():
    extracted = ExtractedOrder(
        po_number="PO-9001",
        buyer_name="Acme Corp",
        ship_to="1 Main St",
        line_items=[LineItem(sku="SKU-1001", description="Widget", quantity=5, unit_price=10.0)],
    )
    return process_order(extracted)


def _finish_call(**overrides) -> _FakeToolCall:
    args = {
        "items": [
            {
                "sku": "SKU-1001",
                "description": "Widget",
                "requested_qty": 5,
                "fulfilled_qty": 5,
                "status": "fulfilled",
            }
        ],
        "order_status": "shipped",
        "summary": "Shipped 5 widgets.",
        **overrides,
    }
    return _FakeToolCall(id="call_1", function=_FakeFunctionCall(name="record_fulfillment_result", arguments=json.dumps(args)))


@pytest.mark.asyncio
async def test_happy_path_ships_and_returns_tracking_number(monkeypatch) -> None:
    tool_call = _FakeToolCall(
        id="call_0", function=_FakeFunctionCall(name="wms__get_inventory_status", arguments=json.dumps({"sku": "SKU-1001"}))
    )
    responses = [
        _FakeMessage(tool_calls=[tool_call]),
        _FakeMessage(
            tool_calls=[
                _finish_call(
                    shipment={
                        "carrier": "UPS",
                        "tracking_number": "1Z999",
                        "estimated_delivery": "2026-08-05",
                    }
                )
            ]
        ),
    ]
    fake_client = _FakeOpenAIClient(responses)
    monkeypatch.setattr(fulfillment_module, "OpenAI", lambda api_key, base_url=None: fake_client)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")

    tools = _FakeTools(tool_results={"wms__get_inventory_status": json.dumps({"on_hand_qty": 120})})

    result = await fulfill_order(_order(), tools)

    assert result.order_status == "shipped"
    assert result.shipment.tracking_number == "1Z999"
    assert tools.calls == [("wms__get_inventory_status", {"sku": "SKU-1001"})]


@pytest.mark.asyncio
async def test_on_event_fires_for_tool_calls_and_final_result(monkeypatch) -> None:
    tool_call = _FakeToolCall(
        id="call_0", function=_FakeFunctionCall(name="wms__get_inventory_status", arguments=json.dumps({"sku": "SKU-1001"}))
    )
    responses = [
        _FakeMessage(tool_calls=[tool_call]),
        _FakeMessage(
            tool_calls=[
                _finish_call(
                    shipment={
                        "carrier": "UPS",
                        "tracking_number": "1Z999",
                        "estimated_delivery": "2026-08-05",
                    }
                )
            ]
        ),
    ]
    fake_client = _FakeOpenAIClient(responses)
    monkeypatch.setattr(fulfillment_module, "OpenAI", lambda api_key, base_url=None: fake_client)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")

    tools = _FakeTools(tool_results={"wms__get_inventory_status": json.dumps({"on_hand_qty": 120})})
    events: list[tuple[str, dict]] = []

    async def on_event(event_type: str, data: dict) -> None:
        events.append((event_type, data))

    result = await fulfill_order(_order(), tools, on_event=on_event)

    assert result.order_status == "shipped"
    assert [event_type for event_type, _ in events] == ["tool_call", "fulfillment_result"]
    assert events[0][1] == {
        "name": "wms__get_inventory_status",
        "arguments": {"sku": "SKU-1001"},
        "ok": True,
        "result": json.dumps({"on_hand_qty": 120}),
    }
    assert events[1][1]["order_status"] == "shipped"


@pytest.mark.asyncio
async def test_escalation_path_records_supervisor_request(monkeypatch) -> None:
    tool_call = _FakeToolCall(
        id="call_0",
        function=_FakeFunctionCall(name="supervisor__request_help", arguments=json.dumps({"question": "SKU-9999 unknown"})),
    )
    responses = [
        _FakeMessage(tool_calls=[tool_call]),
        _FakeMessage(
            tool_calls=[
                _finish_call(
                    items=[
                        {
                            "sku": "SKU-9999",
                            "description": "Mystery widget",
                            "requested_qty": 5,
                            "fulfilled_qty": 0,
                            "status": "escalated",
                        }
                    ],
                    order_status="escalated",
                    summary="Escalated unknown SKU.",
                    escalations=[{"sku": "SKU-9999", "question": "SKU-9999 unknown", "help_request_id": 3}],
                )
            ]
        ),
    ]
    fake_client = _FakeOpenAIClient(responses)
    monkeypatch.setattr(fulfillment_module, "OpenAI", lambda api_key, base_url=None: fake_client)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")

    tools = _FakeTools(tool_results={"supervisor__request_help": json.dumps({"id": 3, "status": "open"})})

    result = await fulfill_order(_order(), tools)

    assert result.order_status == "escalated"
    assert result.escalations[0].help_request_id == 3


@pytest.mark.asyncio
async def test_no_tool_call_after_nudge_raises_fulfillment_error(monkeypatch) -> None:
    responses = [_FakeMessage(content="Thinking..."), _FakeMessage(content="Still thinking...")]
    fake_client = _FakeOpenAIClient(responses)
    monkeypatch.setattr(fulfillment_module, "OpenAI", lambda api_key, base_url=None: fake_client)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")

    with pytest.raises(FulfillmentError):
        await fulfill_order(_order(), _FakeTools())


@pytest.mark.asyncio
async def test_exceeding_max_turns_falls_back_to_escalation(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAX_FULFILLMENT_TURNS", 2)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")

    stall_call = _FakeToolCall(
        id="call_x", function=_FakeFunctionCall(name="wms__get_inventory_status", arguments=json.dumps({"sku": "SKU-1001"}))
    )
    responses = [_FakeMessage(tool_calls=[stall_call]), _FakeMessage(tool_calls=[stall_call])]
    fake_client = _FakeOpenAIClient(responses)
    monkeypatch.setattr(fulfillment_module, "OpenAI", lambda api_key, base_url=None: fake_client)

    tools = _FakeTools(
        tool_results={
            "wms__get_inventory_status": json.dumps({"on_hand_qty": 120}),
            "supervisor__request_help": json.dumps({"id": 7}),
        }
    )

    result = await fulfill_order(_order(), tools)

    assert result.order_status == "escalated"
    assert result.escalations[0].help_request_id == 7
    assert any(name == "supervisor__request_help" for name, _ in tools.calls)


@pytest.mark.asyncio
async def test_ship_order_arguments_overridden_from_order_data(monkeypatch) -> None:
    """The model's own customer_name/customer_address must never reach the shipping
    tool verbatim -- the agent always substitutes the values already known from the
    processed order, even if the model got them wrong or omitted them."""
    ship_call = _FakeToolCall(
        id="call_0",
        function=_FakeFunctionCall(
            name="shipping__ship_order",
            arguments=json.dumps(
                {
                    "po_number": "PO-9001",
                    "customer_name": None,
                    "customer_address": None,
                    "items": [{"sku": "SKU-1001", "qty": 5}],
                }
            ),
        ),
    )
    responses = [_FakeMessage(tool_calls=[ship_call]), _FakeMessage(tool_calls=[_finish_call()])]
    fake_client = _FakeOpenAIClient(responses)
    monkeypatch.setattr(fulfillment_module, "OpenAI", lambda api_key, base_url=None: fake_client)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")

    tools = _FakeTools(
        tool_results={
            "shipping__ship_order": json.dumps({"carrier": "UPS", "tracking_number": "1Z999"}),
        }
    )

    await fulfill_order(_order(), tools)

    assert tools.calls == [
        (
            "shipping__ship_order",
            {
                "po_number": "PO-9001",
                "customer_name": "Acme Corp",
                "customer_address": "1 Main St",
                "items": [{"sku": "SKU-1001", "qty": 5}],
            },
        )
    ]


@pytest.mark.asyncio
async def test_ship_order_blocked_when_ship_to_missing(monkeypatch) -> None:
    extracted = ExtractedOrder(
        po_number="PO-9002",
        buyer_name="Acme Corp",
        ship_to=None,
        line_items=[LineItem(sku="SKU-1001", description="Widget", quantity=5, unit_price=10.0)],
    )
    order = process_order(extracted)

    ship_call = _FakeToolCall(
        id="call_0",
        function=_FakeFunctionCall(
            name="shipping__ship_order",
            arguments=json.dumps(
                {
                    "po_number": "PO-9002",
                    "customer_name": "Acme Corp",
                    "customer_address": "1 Main St",
                    "items": [{"sku": "SKU-1001", "qty": 5}],
                }
            ),
        ),
    )
    responses = [
        _FakeMessage(tool_calls=[ship_call]),
        _FakeMessage(
            tool_calls=[
                _finish_call(
                    items=[
                        {
                            "sku": "SKU-1001",
                            "description": "Widget",
                            "requested_qty": 5,
                            "fulfilled_qty": 5,
                            "status": "escalated",
                        }
                    ],
                    order_status="escalated",
                    summary="Escalated: no ship-to address.",
                )
            ]
        ),
    ]
    fake_client = _FakeOpenAIClient(responses)
    monkeypatch.setattr(fulfillment_module, "OpenAI", lambda api_key, base_url=None: fake_client)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")

    tools = _FakeTools()
    events: list[tuple[str, dict]] = []

    async def on_event(event_type: str, data: dict) -> None:
        events.append((event_type, data))

    result = await fulfill_order(order, tools, on_event=on_event)

    assert not any(name == "shipping__ship_order" for name, _ in tools.calls)
    assert events[0][0] == "tool_call"
    assert events[0][1]["ok"] is False
    assert "ship_to" in events[0][1]["result"]
    assert result.order_status == "escalated"


@pytest.mark.asyncio
async def test_missing_api_key_raises_fulfillment_error(monkeypatch) -> None:
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)

    with pytest.raises(FulfillmentError):
        await fulfill_order(_order(), _FakeTools())


def test_policy_prompt_requires_visual_pick_verification_before_shipping() -> None:
    """Locks in intent: the fulfillment policy must route through the robot's
    photo-capture tool and label-api's inference tool before a pick is
    trusted enough to decrement the ledger or ship - not just fetched_qty."""
    prompt = get_prompt("dc-agent.fulfillment.policy_prompt").format()

    assert "robot__get_item_photo" in prompt
    assert "label__infer_sku" in prompt
    assert prompt.index("robot__get_item_photo") < prompt.index("wms__adjust_inventory")
    assert prompt.index("robot__get_item_photo") < prompt.index("shipping__ship_order")
