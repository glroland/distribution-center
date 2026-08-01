"""Crestline Construction -- amber hazard accent, side bar, heavy bold grid."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, Spacer, Table, TableStyle

from src.models import Company, PurchaseOrder
from src.templates import base

COMPANY = Company(
    name="Crestline Construction",
    address_lines=["3300 Quarry Bend Road", "Denver, CO 80216"],
    phone="(303) 555-0155",
    email="purchasing@crestlineconstruction.com",
    accent_color="#f2a600",
    font="Helvetica-Bold",
)

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


def render(po: PurchaseOrder, output_path: str) -> None:
    doc = base.make_doc(output_path, left_margin=base.MARGIN + 0.1 * inch)
    story = []

    dark = colors.HexColor("#1c1c1c")
    accent = colors.HexColor(COMPANY.accent_color)

    story.append(Paragraph(COMPANY.name.upper(), base.style("company", FONT_BOLD, 18, text_color=dark)))
    story.append(
        Paragraph(
            f"{COMPANY.address_lines[0]}, {COMPANY.address_lines[1]}  |  {COMPANY.phone}  |  {COMPANY.email}",
            base.style("company_sub", FONT, 8.5, text_color=colors.HexColor("#444444")),
        )
    )
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=4, color=accent, spaceAfter=16))

    story.append(Paragraph("PURCHASE ORDER", base.style("title", FONT_BOLD, 15, text_color=dark)))
    story.append(Spacer(1, 0.18 * inch))

    info = Table(
        [
            [
                base.address_block("VENDOR", [po.vendor.name, *po.vendor.address_lines, po.vendor.phone], FONT, FONT_BOLD),
                base.address_block("JOB SITE / SHIP TO", po.ship_to, FONT, FONT_BOLD),
                base.po_meta_block(po, FONT, FONT_BOLD, label_color=colors.HexColor("#8a6d1a")),
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
            header_bg=dark,
            header_fg=accent,
            grid_style="grid",
            accent_color=COMPANY.accent_color,
        )
    )
    story.append(Spacer(1, 0.18 * inch))
    story.append(base.totals_table(po, font_name=FONT, bold_font_name=FONT_BOLD, accent_color=COMPANY.accent_color))
    story.append(Spacer(1, 0.4 * inch))
    story.append(
        Paragraph(
            "Materials must be delivered to the job site trailer between 6am-2pm. Site safety orientation required "
            "for all delivery personnel.",
            base.style("footer", FONT, 8, text_color=colors.grey),
        )
    )

    doc.build(story, onFirstPage=base.side_bar_callback(accent, width=0.2 * inch), onLaterPages=base.side_bar_callback(accent, width=0.2 * inch))
