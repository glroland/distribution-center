"""Northwind Traders -- classic centered serif letterhead, navy double rule."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, Spacer, Table, TableStyle

from src.models import Company, PurchaseOrder
from src.templates import base

COMPANY = Company(
    name="Northwind Traders",
    address_lines=["88 Harbor Mill Lane", "Portland, ME 04101"],
    phone="(207) 555-0138",
    email="orders@northwindtraders.com",
    accent_color="#1a2f4b",
    font="Times-Roman",
)

FONT = "Times-Roman"
FONT_BOLD = "Times-Bold"


def render(po: PurchaseOrder, output_path: str) -> None:
    doc = base.make_doc(output_path)
    story = []

    accent = colors.HexColor(COMPANY.accent_color)

    story.append(Paragraph(COMPANY.name, base.style("company", FONT_BOLD, 20, text_color=accent, alignment=1)))
    story.append(
        Paragraph(
            f"{COMPANY.address_lines[0]} &bull; {COMPANY.address_lines[1]} &bull; {COMPANY.phone} &bull; {COMPANY.email}",
            base.style("company_sub", FONT, 9, alignment=1, text_color=colors.HexColor("#444444")),
        )
    )
    story.append(Spacer(1, 0.1 * inch))
    story.append(HRFlowable(width="100%", thickness=1.5, color=accent, spaceBefore=2, spaceAfter=1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=accent, spaceBefore=1, spaceAfter=14))

    story.append(Paragraph("PURCHASE ORDER", base.style("title", FONT_BOLD, 14, alignment=1, text_color=colors.black)))
    story.append(Spacer(1, 0.2 * inch))

    info = Table(
        [
            [
                base.address_block("Vendor", [po.vendor.name, *po.vendor.address_lines, po.vendor.phone], FONT, FONT_BOLD),
                base.address_block("Ship To", po.ship_to, FONT, FONT_BOLD),
                base.po_meta_block(po, FONT, FONT_BOLD, label_color=colors.HexColor("#555555")),
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
            header_bg=colors.white,
            header_fg=colors.black,
            grid_style="lines",
            accent_color=COMPANY.accent_color,
        )
    )
    story.append(Spacer(1, 0.18 * inch))
    story.append(base.totals_table(po, font_name=FONT, bold_font_name=FONT_BOLD, accent_color=COMPANY.accent_color))
    story.append(Spacer(1, 0.45 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey, spaceAfter=8))
    story.append(
        Paragraph(
            "Terms and conditions of the governing purchase agreement apply. Kindly confirm receipt of this order "
            "and expected ship date within three business days.",
            base.style("footer", FONT, 8, text_color=colors.grey, alignment=1),
        )
    )

    doc.build(story)
