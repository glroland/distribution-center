"""Civic Municipal Procurement Office -- austere bordered government form, monochrome."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, Spacer, Table, TableStyle

from src.models import Company, PurchaseOrder
from src.templates import base

COMPANY = Company(
    name="City of Cedarhollow -- Procurement Office",
    address_lines=["100 Municipal Plaza, Room 210", "Cedarhollow, IL 61820"],
    phone="(217) 555-0110",
    email="procurement@cedarhollow.gov",
    accent_color="#000000",
    font="Times-Roman",
)

FONT = "Times-Roman"
FONT_BOLD = "Times-Bold"


def render(po: PurchaseOrder, output_path: str) -> None:
    doc = base.make_doc(
        output_path,
        top_margin=base.MARGIN + 0.15 * inch,
        bottom_margin=base.MARGIN + 0.15 * inch,
        left_margin=base.MARGIN + 0.15 * inch,
        right_margin=base.MARGIN + 0.15 * inch,
    )
    story = []

    story.append(Paragraph(COMPANY.name, base.style("company", FONT_BOLD, 14, alignment=1)))
    story.append(
        Paragraph(
            f"{COMPANY.address_lines[0]} &bull; {COMPANY.address_lines[1]}<br/>{COMPANY.phone} &bull; {COMPANY.email}",
            base.style("company_sub", FONT, 9, alignment=1),
        )
    )
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black, spaceAfter=10))

    story.append(Paragraph("OFFICIAL PURCHASE ORDER", base.style("title", FONT_BOLD, 13, alignment=1)))
    story.append(
        Paragraph(
            "Issued pursuant to municipal procurement code &sect; 14-220",
            base.style("subtitle", FONT, 8, alignment=1, text_color=colors.HexColor("#444444")),
        )
    )
    story.append(Spacer(1, 0.22 * inch))

    info = Table(
        [
            [
                base.address_block("Vendor", [po.vendor.name, *po.vendor.address_lines, po.vendor.phone], FONT, FONT_BOLD),
                base.address_block("Deliver To", po.ship_to, FONT, FONT_BOLD),
                base.po_meta_block(po, FONT, FONT_BOLD, label_color=colors.HexColor("#444444")),
            ]
        ],
        colWidths=[2.4 * inch, 2.0 * inch, 2.0 * inch],
    )
    info.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.black),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(info)
    story.append(Spacer(1, 0.3 * inch))

    story.append(
        base.line_items_table(
            po,
            font_name=FONT,
            header_font_name=FONT_BOLD,
            header_bg=colors.white,
            header_fg=colors.black,
            grid_style="lines",
            accent_color="#000000",
        )
    )
    story.append(Spacer(1, 0.18 * inch))
    story.append(base.totals_table(po, font_name=FONT, bold_font_name=FONT_BOLD, accent_color="#000000"))
    story.append(Spacer(1, 0.45 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.black, spaceAfter=8))
    story.append(
        Paragraph(
            "This order is subject to all applicable municipal purchasing regulations and appropriation limits. "
            "Invoices must reference the PO number above to be processed for payment.",
            base.style("footer", FONT, 8, alignment=1, text_color=colors.HexColor("#333333")),
        )
    )

    border = base.bordered_page_callback("#000000")
    doc.build(story, onFirstPage=border, onLaterPages=border)
