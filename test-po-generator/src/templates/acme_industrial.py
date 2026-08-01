"""Acme Industrial Supply -- bold orange banner, heavy grid table."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from src.models import Company, PurchaseOrder
from src.templates import base

COMPANY = Company(
    name="Acme Industrial Supply",
    address_lines=["1200 Foundry Parkway", "Cleveland, OH 44114"],
    phone="(216) 555-0192",
    email="purchasing@acmeindustrial.com",
    accent_color="#e2691b",
    font="Helvetica-Bold",
)

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


def render(po: PurchaseOrder, output_path: str) -> None:
    doc = base.make_doc(output_path, top_margin=0.4 * inch)
    story = []

    story.append(
        Paragraph(
            COMPANY.name.upper(),
            base.style("company", FONT_BOLD, 22, text_color=colors.white),
        )
    )
    story.append(
        Paragraph(
            f"{COMPANY.address_lines[0]}, {COMPANY.address_lines[1]} &nbsp;|&nbsp; {COMPANY.phone} &nbsp;|&nbsp; {COMPANY.email}",
            base.style("company_sub", FONT, 9, text_color=colors.white),
        )
    )
    story.append(Spacer(1, 0.55 * inch))

    story.append(Paragraph("PURCHASE ORDER", base.style("title", FONT_BOLD, 16, text_color=colors.HexColor(COMPANY.accent_color))))
    story.append(Spacer(1, 0.15 * inch))

    info = Table(
        [
            [
                base.address_block("VENDOR", [po.vendor.name, *po.vendor.address_lines, po.vendor.phone], FONT, FONT_BOLD),
                base.address_block("SHIP TO", po.ship_to, FONT, FONT_BOLD),
                base.po_meta_block(po, FONT, FONT_BOLD, label_color=colors.HexColor("#8a5a2b")),
            ]
        ],
        colWidths=[2.4 * inch, 2.0 * inch, 2.0 * inch],
    )
    info.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(info)
    story.append(Spacer(1, 0.3 * inch))

    story.append(
        base.line_items_table(
            po,
            font_name=FONT,
            header_font_name=FONT_BOLD,
            header_bg=COMPANY.accent_color,
            header_fg=colors.white,
            grid_style="grid",
            accent_color=COMPANY.accent_color,
        )
    )
    story.append(Spacer(1, 0.18 * inch))
    story.append(base.totals_table(po, font_name=FONT, bold_font_name=FONT_BOLD, accent_color=COMPANY.accent_color))
    story.append(Spacer(1, 0.4 * inch))
    story.append(
        Paragraph(
            "Please reference the PO number above on all invoices, packing slips, and correspondence. "
            "Report any discrepancies to purchasing within 5 business days of receipt.",
            base.style("footer", FONT, 8, text_color=colors.grey),
        )
    )

    banner = base.banner_callback(COMPANY.accent_color)
    doc.build(story, onFirstPage=banner, onLaterPages=banner)
