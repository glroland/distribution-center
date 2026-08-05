import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import mlflow
from openai import OpenAI
from pydantic import ValidationError

from .mcp_tools import McpToolRouter, ToolCallError
from .models import Escalation, FulfillmentItemResult, FulfillmentResult, ProcessOrderResult
from .settings import settings
from .tracing import configure_tracing

configure_tracing()

logger = logging.getLogger(__name__)

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
(carrier handoff), `supervisor__*` (escalate to a human), and `label__*` \
(local SKU image inference, used to visually verify a pick).

Follow this policy:
1. For every line item, check on-hand quantity via `wms__get_inventory_status` \
before doing anything physical. Never assume stock exists.
2. For every item with enough on-hand quantity, call \
`robot__plan_and_fetch_items` exactly once with the full list of \
{"sku", "qty"} pairs to retrieve. It handles visiting order, movement, \
picking, capacity-driven dock round-trips, and final delivery on its own.
3. Treat the `fetched_qty` reported per SKU in `robot__plan_and_fetch_items`'s \
response as the source of truth for quantity retrieved, not the requested \
quantity. `fetched_qty` below `requested_qty` is not a failure to recover \
from — it just means that SKU wasn't fully stocked on the shelves; handle it \
like any other shortfall (step 7).
4. Before shipping or decrementing anything, visually verify each SKU with \
`fetched_qty` > 0: call `robot__get_item_photo` with that SKU to capture a \
photo of its shelf sticker, then pass the response's `image_id` straight \
to `label__infer_sku` to read what the sticker actually says. Count the pick \
as verified only if the returned `sku` matches the SKU you asked \
`get_item_photo` for and `confidence` is reasonably high (roughly 0.5 or \
above). A different SKU, an empty read, or low confidence means the physical \
item doesn't back up what the shelf was supposed to hold — a mispick or a \
mislabeled shelf — so treat that item's whole `fetched_qty` as unverified: \
exclude it from steps 5-6 for that SKU and handle it as a shortfall in step 7 \
instead, same as if it had never been fetched. Do this once per distinct SKU \
fetched, not once per unit.
5. For each SKU that passed verification, decrement the WMS ledger by its \
fetched quantity via `wms__adjust_inventory` (negative delta), so the \
warehouse's book of record matches what physically left the shelf.
6. Once picking and verification are done, ship everything that passed \
verification in a single `shipping__ship_order` call, using the order's \
buyer_name as customer_name (or "Recipient" if the order has no buyer_name) \
and its ship_to address as customer_address.
7. For any line item that comes up short — because the WMS didn't have \
enough on-hand quantity in the first place, because `plan_and_fetch_items` \
reported `fetched_qty` below what the WMS said was on hand (a data mismatch \
between the ledger and the physical shelves), or because step 4's visual \
verification failed for it (a mispick or mislabeled shelf, not a quantity \
problem) — first try `supervisor__request_transfer` with the SKU and the \
shortfall quantity before escalating to a human. A verification failure is \
still a "shortfall" in the sense that nothing trustworthy was fetched for \
that SKU, even though the ledger showed enough stock:
   - If it returns status `available`, the shortfall is inbound from \
another DC at `source_location`. Use `robot__restock_shelf` to place the \
transferred quantity on a shelf (omit x/y to let it pick one automatically), \
then call `robot__plan_and_fetch_items` again with just that SKU and the \
transferred quantity, and fold the result into that item's delivery like any \
other stock (steps 3-6 still apply, including verification and the WMS \
ledger decrement for what's actually fetched).
   - If it returns status `unavailable`, or the SKU is unknown to the WMS in \
the first place, call `supervisor__request_help` with a clear question \
(include the SKU, quantity requested, quantity on hand, and — if this came \
from a failed verification — what label__infer_sku actually read) and move \
on — never let one bad line item block the rest of the order.
8. When every line item has been either shipped or escalated, call \
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


@mlflow.trace(span_type="AGENT", name="fulfill_order")
async def fulfill_order(
    order: ProcessOrderResult, tools: McpToolRouter, on_event: OnEvent | None = None
) -> FulfillmentResult:
    if not settings.OPENAI_API_KEY:
        raise FulfillmentError("OPENAI_API_KEY is not configured")

    client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL or None,
        timeout=settings.OPENAI_REQUEST_TIMEOUT_SECONDS,
    )
    available_tools = tools.list_openai_tools() + [_FINISH_TOOL_SCHEMA]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(tools)},
        {"role": "user", "content": _order_message(order)},
    ]

    logger.info("Starting fulfillment for PO %s (%d line items)", order.po_number, len(order.line_items))

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
            logger.exception("OpenAI API call failed during fulfillment of PO %s", order.po_number)
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
                logger.info(
                    "Fulfillment finished for PO %s: status=%s, %d escalation(s)",
                    order.po_number, finish_result.order_status, len(finish_result.escalations),
                )
                if on_event:
                    await on_event("fulfillment_result", finish_result.model_dump())
                return finish_result

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": await _run_tool_call(tools, tool_call, order, on_event),
                }
            )

    return await _escalate_timeout(order, tools, on_event)


async def _run_tool_call(
    tools: McpToolRouter, tool_call: Any, order: ProcessOrderResult, on_event: OnEvent | None = None
) -> str:
    name = tool_call.function.name
    try:
        arguments = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError as exc:
        error = f"Invalid tool arguments: {exc}"
        logger.warning("Tool call %s had invalid arguments: %s", name, exc)
        if on_event:
            await on_event("tool_call", {"name": name, "arguments": {}, "ok": False, "result": error})
        return json.dumps({"error": error})

    if name == "shipping__ship_order":
        if not order.ship_to:
            error = (
                "Cannot ship: this order is missing ship_to. "
                "Escalate via supervisor__request_help instead of shipping without that information."
            )
            logger.warning("Tool call %s blocked for PO %s: %s", name, order.po_number, error)
            if on_event:
                await on_event("tool_call", {"name": name, "arguments": arguments, "ok": False, "result": error})
            return json.dumps({"error": error})
        # The order's buyer_name/ship_to are known good; don't trust the model to have
        # copied them correctly into the tool call. buyer_name is optional on the order,
        # so fall back to a generic recipient rather than blocking the shipment.
        arguments["customer_name"] = order.buyer_name or "Recipient"
        arguments["customer_address"] = order.ship_to

    try:
        result = await tools.call(name, arguments)
        logger.info("Tool call %s(%s) succeeded", name, arguments)
        if on_event:
            await on_event("tool_call", {"name": name, "arguments": arguments, "ok": True, "result": result})
        return result
    except ToolCallError as exc:
        logger.warning("Tool call %s(%s) failed: %s", name, arguments, exc)
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
    logger.warning(
        "PO %s exceeded %d fulfillment turns without finishing; escalating",
        order.po_number, settings.MAX_FULFILLMENT_TURNS,
    )
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
