"""Blue Ridge Supply Co. -- minimal black & white, no color, thin rules only."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, Spacer, Table, TableStyle

from src.models import Company, PurchaseOrder
from src.templates import base

COMPANY = Company(
    name="Blue Ridge Supply Co.",
    address_lines=["17 Ridgeline Drive", "Asheville, NC 28801"],
    phone="(828) 555-0104",
    email="orders@blueridgesupply.com",
    accent_color="#000000",
    font="Helvetica",
)

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


def render(po: PurchaseOrder, output_path: str) -> None:
    doc = base.make_doc(output_path)
    story = []

    story.append(Paragraph(COMPANY.name, base.style("company", FONT_BOLD, 18, text_color=colors.black)))
    story.append(
        Paragraph(
            f"{COMPANY.address_lines[0]}, {COMPANY.address_lines[1]}  |  {COMPANY.phone}  |  {COMPANY.email}",
            base.style("company_sub", FONT, 8.5, text_color=colors.HexColor("#333333")),
        )
    )
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=0.75, color=colors.black, spaceAfter=16))

    story.append(Paragraph("Purchase Order", base.style("title", FONT_BOLD, 13)))
    story.append(Spacer(1, 0.18 * inch))

    info = Table(
        [
            [
                base.address_block("Vendor", [po.vendor.name, *po.vendor.address_lines, po.vendor.phone], FONT, FONT_BOLD),
                base.address_block("Ship To", po.ship_to, FONT, FONT_BOLD),
                base.po_meta_block(po, FONT, FONT_BOLD, label_color=colors.HexColor("#666666")),
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
    story.append(
        Paragraph(
            "Questions about this order? Contact purchasing at the phone number or email above.",
            base.style("footer", FONT, 8, text_color=colors.HexColor("#666666")),
        )
    )

    doc.build(story)
