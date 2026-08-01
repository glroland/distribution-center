from typing import Literal

from pydantic import BaseModel


class LineItem(BaseModel):
    sku: str | None = None
    description: str
    quantity: float
    unit_price: float

    @property
    def line_total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


class ExtractedOrder(BaseModel):
    """Purchase order fields as extracted from the PDF's markdown by the LLM."""

    po_number: str
    issue_date: str | None = None
    vendor_name: str | None = None
    buyer_name: str | None = None
    ship_to: str | None = None
    payment_terms: str | None = None
    line_items: list[LineItem]
    stated_subtotal: float | None = None
    stated_tax: float | None = None
    stated_total: float | None = None


class Shipment(BaseModel):
    """A shipment created by local-shipping-api for a fulfilled order."""

    carrier: str
    tracking_number: str
    estimated_delivery: str


class FulfillmentItemResult(BaseModel):
    """Outcome of trying to fulfil one PO line item from the DC's inventory."""

    sku: str | None = None
    description: str
    requested_qty: float
    fulfilled_qty: float
    status: Literal["fulfilled", "partial", "out_of_stock", "escalated"]
    note: str | None = None


class Escalation(BaseModel):
    """A supervisor help request raised while fulfilling an order."""

    sku: str | None = None
    question: str
    help_request_id: int | None = None


class FulfillmentResult(BaseModel):
    """Outcome of the fulfillment agent's attempt to pick and ship an order."""

    items: list[FulfillmentItemResult]
    shipment: Shipment | None = None
    escalations: list[Escalation] = []
    order_status: Literal["shipped", "partially_shipped", "escalated", "failed"]
    summary: str


class ProcessOrderResult(BaseModel):
    """The distribution center's processed record of an inbound purchase order."""

    dc_order_id: str
    po_number: str
    issue_date: str | None = None
    vendor_name: str | None = None
    buyer_name: str | None = None
    ship_to: str | None = None
    payment_terms: str | None = None
    line_items: list[LineItem]
    computed_subtotal: float
    stated_total: float | None = None
    totals_mismatch: bool
    fulfillment: FulfillmentResult | None = None
