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
