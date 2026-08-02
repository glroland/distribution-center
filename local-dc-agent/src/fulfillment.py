import json
from collections.abc import Awaitable, Callable
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from .mcp_tools import McpToolRouter, ToolCallError
from .models import Escalation, FulfillmentItemResult, FulfillmentResult, ProcessOrderResult
from .settings import settings

OnEvent = Callable[[str, dict], Awaitable[None]]

_FINISH_TOOL_NAME = "record_fulfillment_result"

_FINISH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": _FINISH_TOOL_NAME,
        "description": (
            "Report the final outcome of fulfilling this purchase order. Call this "
            "exactly once, as your last action, after every line item has either been "
            "shipped or escalated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "One entry per PO line item.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string"},
                            "description": {"type": "string"},
                            "requested_qty": {"type": "number"},
                            "fulfilled_qty": {
                                "type": "number",
                                "description": "Quantity actually delivered to the dock, per deliver_items.",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["fulfilled", "partial", "out_of_stock", "escalated"],
                            },
                            "note": {"type": "string"},
                        },
                        "required": ["description", "requested_qty", "fulfilled_qty", "status"],
                    },
                },
                "shipment": {
                    "type": "object",
                    "description": "Omit if nothing was shipped (e.g. the whole order was escalated).",
                    "properties": {
                        "carrier": {"type": "string"},
                        "tracking_number": {"type": "string"},
                        "estimated_delivery": {"type": "string"},
                    },
                    "required": ["carrier", "tracking_number", "estimated_delivery"],
                },
                "escalations": {
                    "type": "array",
                    "description": "One entry per supervisor help request raised, if any.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string"},
                            "question": {"type": "string"},
                            "help_request_id": {"type": "integer"},
                        },
                        "required": ["question"],
                    },
                },
                "order_status": {
                    "type": "string",
                    "enum": ["shipped", "partially_shipped", "escalated", "failed"],
                },
                "summary": {"type": "string", "description": "One or two sentence human-readable summary."},
            },
            "required": ["items", "order_status", "summary"],
        },
    },
}

_POLICY_PROMPT = """\
You are the fulfillment agent for a distribution center. You have just been \
handed a parsed purchase order and must physically fulfil it using the tools \
available to you, then report the outcome.

Tool names are prefixed by which service they belong to: `wms__*` (warehouse \
inventory ledger), `robot__*` (the physical picking robot), `shipping__*` \
(carrier handoff), and `supervisor__*` (escalate to a human).

Follow this policy:
1. For every line item, check on-hand quantity via `wms__get_inventory_status` \
before doing anything physical. Never assume stock exists.
2. For each item with enough on-hand quantity, use the robot tools to \
physically retrieve it: `robot__find_item` to locate a shelf, \
`robot__move_robot` to it, `robot__fetch_item` to pick it up. Check \
`robot__get_robot_status` if you're unsure how much carry capacity remains — \
if it would be exceeded, return to the dock and `robot__deliver_items` first, \
then go back out for the rest. Once everything fetchable is loaded, \
`robot__move_robot` back to the dock and call `robot__deliver_items`.
3. Treat what `robot__deliver_items` reports as actually delivered as the \
source of truth for quantity retrieved, not the requested quantity.
4. For each SKU actually delivered, decrement the WMS ledger by that delivered \
quantity via `wms__adjust_inventory` (negative delta), so the warehouse's \
book of record matches what physically left the shelf.
5. Once picking is done, ship everything delivered in a single \
`shipping__ship_order` call, using the order's buyer_name as customer_name \
and its ship_to address as customer_address.
6. For any line item that is unknown to the WMS, or doesn't have enough \
on-hand quantity to fulfil the requested amount, call \
`supervisor__request_help` with a clear question (include the SKU, quantity \
requested, and quantity on hand) and move on — never let one bad line item \
block the rest of the order.
7. When every line item has been either shipped or escalated, call \
`record_fulfillment_result` exactly once, as your final action, summarizing \
every item's outcome, the shipment created (if any), and any escalations \
raised.
"""


class FulfillmentError(Exception):
    """Raised when the fulfillment agent loop fails outright."""


def _build_system_prompt(tools: McpToolRouter) -> str:
    sections = [_POLICY_PROMPT, "Tool server reference (as declared by each connected server):"]
    for label, instructions in tools.server_instructions().items():
        sections.append(f"- {label}: {instructions}")
    return "\n".join(sections)


def _order_message(order: ProcessOrderResult) -> str:
    payload = {
        "po_number": order.po_number,
        "buyer_name": order.buyer_name,
        "ship_to": order.ship_to,
        "line_items": [
            {"sku": item.sku, "description": item.description, "quantity": item.quantity}
            for item in order.line_items
        ],
    }
    return json.dumps(payload)


async def fulfill_order(
    order: ProcessOrderResult, tools: McpToolRouter, on_event: OnEvent | None = None
) -> FulfillmentResult:
    if not settings.OPENAI_API_KEY:
        raise FulfillmentError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    available_tools = tools.list_openai_tools() + [_FINISH_TOOL_SCHEMA]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(tools)},
        {"role": "user", "content": _order_message(order)},
    ]

    nudged = False
    for _ in range(settings.MAX_FULFILLMENT_TURNS):
        try:
            response = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                tools=available_tools,
                tool_choice="auto",
                messages=messages,
            )
        except Exception as exc:  # openai.APIError and friends
            raise FulfillmentError(f"OpenAI API call failed: {exc}") from exc

        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        tool_calls = message.tool_calls or []
        if not tool_calls:
            if nudged:
                raise FulfillmentError("Model stopped calling tools without finishing fulfillment")
            nudged = True
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Continue fulfilling this order. Call the next tool you need, or "
                        f"call {_FINISH_TOOL_NAME} if you are done."
                    ),
                }
            )
            continue

        for tool_call in tool_calls:
            if tool_call.function.name == _FINISH_TOOL_NAME:
                finish_result = _parse_finish_call(tool_call.function.arguments)
                if on_event:
                    await on_event("fulfillment_result", finish_result.model_dump())
                return finish_result

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": await _run_tool_call(tools, tool_call, on_event),
                }
            )

    return await _escalate_timeout(order, tools, on_event)


async def _run_tool_call(tools: McpToolRouter, tool_call: Any, on_event: OnEvent | None = None) -> str:
    name = tool_call.function.name
    try:
        arguments = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError as exc:
        error = f"Invalid tool arguments: {exc}"
        if on_event:
            await on_event("tool_call", {"name": name, "arguments": {}, "ok": False, "result": error})
        return json.dumps({"error": error})

    try:
        result = await tools.call(name, arguments)
        if on_event:
            await on_event("tool_call", {"name": name, "arguments": arguments, "ok": True, "result": result})
        return result
    except ToolCallError as exc:
        if on_event:
            await on_event(
                "tool_call", {"name": name, "arguments": arguments, "ok": False, "result": str(exc)}
            )
        return json.dumps({"error": str(exc)})


def _parse_finish_call(arguments_json: str) -> FulfillmentResult:
    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise FulfillmentError(f"{_FINISH_TOOL_NAME} arguments were not valid JSON: {exc}") from exc

    try:
        return FulfillmentResult.model_validate(arguments)
    except ValidationError as exc:
        raise FulfillmentError(f"{_FINISH_TOOL_NAME} arguments failed schema validation: {exc}") from exc


async def _escalate_timeout(
    order: ProcessOrderResult, tools: McpToolRouter, on_event: OnEvent | None = None
) -> FulfillmentResult:
    """Fallback when the model never finishes: escalate directly and degrade gracefully
    rather than failing the whole PO."""
    question = (
        f"Fulfillment agent exceeded {settings.MAX_FULFILLMENT_TURNS} tool-call turns while "
        f"processing PO {order.po_number} and did not finish. Manual review needed."
    )
    arguments = {"question": question, "context": order.po_number}
    help_request_id = None
    try:
        raw = await tools.call("supervisor__request_help", arguments)
        if on_event:
            await on_event(
                "tool_call",
                {"name": "supervisor__request_help", "arguments": arguments, "ok": True, "result": raw},
            )
        help_request_id = json.loads(raw).get("id")
    except ToolCallError as exc:
        if on_event:
            await on_event(
                "tool_call",
                {
                    "name": "supervisor__request_help",
                    "arguments": arguments,
                    "ok": False,
                    "result": str(exc),
                },
            )

    result = FulfillmentResult(
        items=[
            FulfillmentItemResult(
                sku=item.sku,
                description=item.description,
                requested_qty=item.quantity,
                fulfilled_qty=0,
                status="escalated",
                note="Fulfillment agent did not finish in time.",
            )
            for item in order.line_items
        ],
        shipment=None,
        escalations=[Escalation(question=question, help_request_id=help_request_id)],
        order_status="escalated",
        summary=question,
    )
    if on_event:
        await on_event("fulfillment_result", result.model_dump())
    return result
