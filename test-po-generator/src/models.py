"""Data models shared across catalog loading, numbering, and templates."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Company:
    """A company identity used either as the PO-issuing letterhead or as a vendor."""

    name: str
    address_lines: list[str]
    phone: str
    email: str
    accent_color: str = "#1a3c6e"
    font: str = "Helvetica"


@dataclass
class LineItem:
    sku: str
    description: str
    quantity: int
    unit_price: float

    @property
    def line_total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass
class PurchaseOrder:
    po_number: str
    issue_date: str
    buyer: Company
    vendor: Company
    ship_to: list[str]
    payment_terms: str
    buyer_contact: str
    line_items: list[LineItem] = field(default_factory=list)
    tax_rate: float = 0.0

    @property
    def subtotal(self) -> float:
        return round(sum(item.line_total for item in self.line_items), 2)

    @property
    def tax(self) -> float:
        return round(self.subtotal * self.tax_rate, 2)

    @property
    def total(self) -> float:
        return round(self.subtotal + self.tax, 2)
