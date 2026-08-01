import uuid

from .models import ExtractedOrder, ProcessOrderResult

_MISMATCH_TOLERANCE = 0.01


def process_order(order: ExtractedOrder) -> ProcessOrderResult:
    """Turn an LLM-extracted order into the DC's processed order record."""
    computed_subtotal = round(sum(item.line_total for item in order.line_items), 2)

    totals_mismatch = False
    if order.stated_total is not None:
        expected_total = computed_subtotal + (order.stated_tax or 0)
        totals_mismatch = abs(expected_total - order.stated_total) > _MISMATCH_TOLERANCE

    dc_order_id = f"dc-{order.po_number}-{uuid.uuid4().hex[:6]}"

    return ProcessOrderResult(
        dc_order_id=dc_order_id,
        po_number=order.po_number,
        issue_date=order.issue_date,
        vendor_name=order.vendor_name,
        buyer_name=order.buyer_name,
        ship_to=order.ship_to,
        payment_terms=order.payment_terms,
        line_items=order.line_items,
        computed_subtotal=computed_subtotal,
        stated_total=order.stated_total,
        totals_mismatch=totals_mismatch,
    )


def summarize(result: ProcessOrderResult) -> str:
    vendor = result.vendor_name or "an unknown vendor"
    total = result.stated_total if result.stated_total is not None else result.computed_subtotal
    summary = (
        f"Processed PO {result.po_number} from {vendor} — "
        f"{len(result.line_items)} line item(s), ${total:,.2f} total. "
        f"DC order ID: {result.dc_order_id}."
    )
    if result.totals_mismatch:
        summary += (
            f" WARNING: stated total (${result.stated_total:,.2f}) does not match "
            f"the computed line-item subtotal (${result.computed_subtotal:,.2f})."
        )
    return summary
