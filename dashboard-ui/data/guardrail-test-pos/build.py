"""Demo PO PDF builder for the "Agentic Safety" off/on guardrail demo.

Unlike the checked-in eval-suite/src/pdf_builder.py, every table cell is
wrapped in a Paragraph flowable (not a bare string) so long injected text
wraps within its cell instead of overflowing -- an unwrapped long string in
a reportlab Table cell overflows past the cell/column boundary and overlaps
neighboring rows, which is what made Docling's layout model misclassify the
whole items table as a "Picture" (dropping it from extraction entirely) in
the original GUARDRAIL-TEST-02 PDF. See this directory's README.md ("Why
the previous version of this fixture set didn't work") for the full story
behind every constraint enforced here -- violating any of them silently
drops content from extraction rather than raising an error, so they're not
optional style choices.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


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


_styles = getSampleStyleSheet()
_cell_style = ParagraphStyle("cell", parent=_styles["Normal"], fontSize=8, leading=10)
_hidden_style = ParagraphStyle(
    "hidden", parent=_styles["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#fafafa")
)


def _p(text: str, style: ParagraphStyle = _cell_style) -> Paragraph:
    # Table cells need real Paragraph flowables (not bare strings) so long
    # text wraps inside the cell instead of overflowing into neighbors.
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(escaped, style)


def build_po_pdf(order: PdfOrder, output_path: str | Path, hidden_ship_to_suffix: str | None = None) -> Path:
    """hidden_ship_to_suffix appends a second, near-invisible paragraph after
    the visible ship_to address. Note this does NOT get merged into the
    extracted ship_to field (a second Paragraph under the same heading is
    dropped by extraction, not combined) -- for a hidden clause that must
    survive as part of ship_to itself, embed it as an inline
    `<font color="#fafafa">...</font>` span inside order["ship_to"] instead,
    as GUARDRAIL-TEST-04/06 do. This parameter exists for cases where a
    trailing near-invisible note doesn't need to be read as part of the
    address field itself.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(str(output_path), pagesize=letter)
    story = [
        Paragraph("PURCHASE ORDER", _styles["Title"]),
        Paragraph(f"PO Number: {order['po_number']}", _styles["Normal"]),
        Paragraph(f"Issue Date: {order['issue_date']}", _styles["Normal"]),
        Paragraph(f"Payment Terms: {order['payment_terms']}", _styles["Normal"]),
        Spacer(1, 0.2 * inch),
        Paragraph("VENDOR", _styles["Heading3"]),
        Paragraph(order["vendor_name"], _styles["Normal"]),
        Spacer(1, 0.15 * inch),
        Paragraph("BUYER", _styles["Heading3"]),
        Paragraph(order["buyer_name"], _styles["Normal"]),
        Spacer(1, 0.15 * inch),
        Paragraph("SHIP TO", _styles["Heading3"]),
        Paragraph(order["ship_to"], _styles["Normal"]),
    ]
    if hidden_ship_to_suffix:
        story.append(_p(hidden_ship_to_suffix, _hidden_style))
    story.append(Spacer(1, 0.25 * inch))

    rows = [["SKU", "Description", "Qty", "Unit Price", "Line Total"]]
    subtotal = 0.0
    for item in order["line_items"]:
        line_total = round(item["quantity"] * item["unit_price"], 2)
        subtotal += line_total
        rows.append(
            [
                _p(item["sku"]),
                _p(item["description"]),
                _p(str(item["quantity"])),
                _p(f"${item['unit_price']:.2f}"),
                _p(f"${line_total:.2f}"),
            ]
        )
    subtotal = round(subtotal, 2)
    rows.append(["", "", "", _p("Subtotal"), _p(f"${subtotal:.2f}")])

    # Deliberately unstyled (no GRID/BACKGROUND): a colored, gridded table
    # anywhere on the same page as a very-light-colored (near-invisible)
    # paragraph reliably makes Docling's layout model misclassify that pale
    # text -- and often the table itself -- as a "Picture", dropping it from
    # extraction entirely. A plain table avoids tripping that path.
    table = Table(rows, colWidths=[0.9 * inch, 2.8 * inch, 0.5 * inch, 0.9 * inch, 1.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey),
            ]
        )
    )
    story.append(table)

    doc.build(story)
    return output_path
