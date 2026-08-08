"""A minimal, self-contained purchase-order PDF renderer.

eval-suite needs exact control over what goes into a generated PO (specific
SKUs/quantities chosen against seed_data.py, so the correct outcome is known
ahead of time) -- test-po-generator's random sampling can't be steered that
precisely, and its `src` package name collides with this service's own if
imported in-process (each service here is an independent package, not a
shared library -- see CLAUDE.md's "Per-service shape" section). Visual
fidelity doesn't matter, only that po-ingest-api's Docling conversion can
read it back as clean text/tables, so this stays deliberately plain rather
than mirroring test-po-generator's 12 letterhead templates.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet


class PdfLineItem(TypedDict):
    sku: str
    description: str
    quantity: float
    unit_price: float


class PdfOrder(TypedDict):
    po_number: str
    issue_date: str
    vendor_name: str
    buyer_name: str
    ship_to: str
    payment_terms: str
    line_items: list[PdfLineItem]


def build_po_pdf(order: PdfOrder, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(output_path), pagesize=letter)
    story = [
        Paragraph("PURCHASE ORDER", styles["Title"]),
        Paragraph(f"PO Number: {order['po_number']}", styles["Normal"]),
        Paragraph(f"Issue Date: {order['issue_date']}", styles["Normal"]),
        Paragraph(f"Payment Terms: {order['payment_terms']}", styles["Normal"]),
        Spacer(1, 0.2 * inch),
        Paragraph("VENDOR", styles["Heading3"]),
        Paragraph(order["vendor_name"], styles["Normal"]),
        Spacer(1, 0.15 * inch),
        Paragraph("BUYER", styles["Heading3"]),
        Paragraph(order["buyer_name"], styles["Normal"]),
        Spacer(1, 0.15 * inch),
        Paragraph("SHIP TO", styles["Heading3"]),
        Paragraph(order["ship_to"], styles["Normal"]),
        Spacer(1, 0.25 * inch),
    ]

    rows = [["SKU", "Description", "Qty", "Unit Price", "Line Total"]]
    subtotal = 0.0
    for item in order["line_items"]:
        line_total = round(item["quantity"] * item["unit_price"], 2)
        subtotal += line_total
        rows.append(
            [
                item["sku"],
                item["description"],
                str(item["quantity"]),
                f"${item['unit_price']:.2f}",
                f"${line_total:.2f}",
            ]
        )
    subtotal = round(subtotal, 2)
    rows.append(["", "", "", "Subtotal", f"${subtotal:.2f}"])

    table = Table(rows, colWidths=[1.1 * inch, 2.6 * inch, 0.6 * inch, 1.0 * inch, 1.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3c6e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -2), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    story.append(table)

    doc.build(story)
    return output_path
