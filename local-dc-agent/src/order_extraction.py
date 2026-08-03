import json
import logging
from typing import Any

import mlflow
from openai import OpenAI
from pydantic import ValidationError

from .settings import settings
from .models import ExtractedOrder
from .tracing import configure_tracing

configure_tracing()

logger = logging.getLogger(__name__)

_TOOL_NAME = "record_purchase_order"

_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": "Record the structured fields of a purchase order.",
        "parameters": {
            "type": "object",
            "properties": {
                "po_number": {"type": "string", "description": "The purchase order number/ID."},
                "issue_date": {"type": "string", "description": "The date the PO was issued, as written."},
                "vendor_name": {
                    "type": ["string", "null"],
                    "description": (
                        "The vendor/supplier the order is placed with. Usually appears as an "
                        "unlabeled block of text (company name, then address, then phone) directly "
                        "under a heading like 'VENDOR' or 'Vendor' -- report just the company name "
                        "from that block, not the whole address. Use null only if truly absent from "
                        "the document."
                    ),
                },
                "buyer_name": {"type": "string", "description": "The company issuing the PO."},
                "ship_to": {
                    "type": ["string", "null"],
                    "description": (
                        "The shipping/delivery address, as a single string. Usually appears as an "
                        "unlabeled block of address text directly under a heading such as "
                        "'SHIP TO', 'Ship To', 'Deliver To', or 'Sold To'. This heading and its "
                        "address block can be positioned anywhere in the document -- including "
                        "after the line-items table or totals, not just near the vendor section -- "
                        "so check the entire document for it rather than only the top. It is "
                        "usually different from the vendor's address, but do not skip it just "
                        "because it happens to match the buyer's own letterhead address. Use null "
                        "only if truly absent from the document."
                    ),
                },
                "payment_terms": {"type": "string", "description": "Payment terms, e.g. 'Net 30'."},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {
                                "type": ["string", "null"],
                                "description": (
                                    "The SKU or item/product code for this line item, exactly as "
                                    "printed in its own column/field. Use null if the document has "
                                    "no separate SKU/item-code field for line items -- never reuse "
                                    "the description as a SKU."
                                ),
                            },
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                        },
                        "required": ["sku", "description", "quantity", "unit_price"],
                    },
                },
                "stated_subtotal": {"type": "number", "description": "Subtotal printed on the PO, if any."},
                "stated_tax": {"type": "number", "description": "Tax amount printed on the PO, if any."},
                "stated_total": {"type": "number", "description": "Grand total printed on the PO, if any."},
            },
            "required": ["po_number", "line_items", "vendor_name", "ship_to"],
        },
    },
}

_SYSTEM_PROMPT = (
    "You extract structured purchase order data from the Markdown export of a "
    "scanned/converted PO PDF. Use the record_purchase_order tool to report the "
    "fields you find. Omit fields that are not present in the document rather "
    "than guessing. Layouts vary widely between vendors. Some fields are written "
    "as a markdown heading (e.g. '## VENDOR', '## SHIP TO') immediately followed "
    "by a plain paragraph of text with no 'label: value' formatting -- the "
    "paragraph right after such a heading is that field's value. The PDF-to-"
    "markdown conversion can reorder sections, so a heading and its paragraph "
    "may appear later in the document than you'd expect (e.g. after the line "
    "items or totals) -- read the whole document before deciding a field is "
    "missing."
)


class ExtractionError(Exception):
    """Raised when the LLM fails to return a valid, schema-conforming order."""


@mlflow.trace(span_type="LLM", name="extract_order")
def extract_order(markdown: str) -> ExtractedOrder:
    if not settings.OPENAI_API_KEY:
        raise ExtractionError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL or None)
    logger.info("Extracting order from %d chars of markdown via %s", len(markdown), settings.OPENAI_MODEL)
    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            max_tokens=2048,
            temperature=0,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": markdown},
            ],
        )
    except Exception as exc:  # openai.APIError and friends
        logger.exception("OpenAI API call failed during extraction")
        raise ExtractionError(f"OpenAI API call failed: {exc}") from exc

    tool_input = _find_tool_input(response.choices)
    if tool_input is None:
        logger.error("Model did not return a record_purchase_order tool call")
        raise ExtractionError("Model did not return a record_purchase_order tool call")

    try:
        order = ExtractedOrder.model_validate(tool_input)
    except ValidationError as exc:
        logger.error("Extracted order failed schema validation: %s", exc)
        raise ExtractionError(f"Model output failed schema validation: {exc}") from exc

    logger.info("Extracted PO %s with %d line item(s)", order.po_number, len(order.line_items))
    return order


def _find_tool_input(choices: list[Any]) -> dict[str, Any] | None:
    for choice in choices:
        for tool_call in getattr(choice.message, "tool_calls", None) or []:
            if tool_call.function.name == _TOOL_NAME:
                try:
                    return json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    return None
    return None
