"""Ironclad Fabrication Co. -- rugged industrial look, monospace PO number, side bar."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, Spacer, Table, TableStyle

from src.models import Company, PurchaseOrder
from src.templates import base

COMPANY = Company(
    name="Ironclad Fabrication Co.",
    address_lines=["4820 Foundry Road", "Gary, IN 46402"],
    phone="(219) 555-0147",
    email="orders@ironcladfab.com",
    accent_color="#d99a1b",
    font="Courier",
)

FONT = "Helvetica"
FONT_MONO = "Courier"
FONT_MONO_BOLD = "Courier-Bold"


def render(po: PurchaseOrder, output_path: str) -> None:
    doc = base.make_doc(output_path, left_margin=base.MARGIN + 0.1 * inch)
    story = []

    dark = colors.HexColor("#1a1a1a")
    accent = colors.HexColor(COMPANY.accent_color)

    story.append(Paragraph(COMPANY.name.upper(), base.style("company", FONT_MONO_BOLD, 17, text_color=dark)))
    story.append(
        Paragraph(
            f"{COMPANY.address_lines[0]}, {COMPANY.address_lines[1]}  |  {COMPANY.phone}  |  {COMPANY.email}",
            base.style("company_sub", FONT_MONO, 8, text_color=colors.HexColor("#444444")),
        )
    )
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=3, color=accent, spaceAfter=16))

    story.append(Paragraph("PURCHASE ORDER", base.style("title", FONT_MONO_BOLD, 14, text_color=dark)))
    story.append(Spacer(1, 0.18 * inch))

    info = Table(
        [
            [
                base.address_block("VENDOR", [po.vendor.name, *po.vendor.address_lines, po.vendor.phone], FONT_MONO, FONT_MONO_BOLD),
                base.address_block("SHIP TO", po.ship_to, FONT_MONO, FONT_MONO_BOLD),
                base.po_meta_block(po, FONT_MONO, FONT_MONO_BOLD, label_color=colors.HexColor("#8a6b1f")),
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
            header_font_name=FONT_MONO_BOLD,
            header_bg=dark,
            header_fg=accent,
            grid_style="grid",
            accent_color=COMPANY.accent_color,
        )
    )
    story.append(Spacer(1, 0.18 * inch))
    story.append(base.totals_table(po, font_name=FONT, bold_font_name=FONT_MONO_BOLD, accent_color=COMPANY.accent_color))
    story.append(Spacer(1, 0.4 * inch))
    story.append(
        Paragraph(
            "All fabricated parts subject to incoming dimensional inspection. Mill certs required for raw stock.",
            base.style("footer", FONT_MONO, 8, text_color=colors.grey),
        )
    )

    doc.build(story, onFirstPage=base.side_bar_callback(dark, width=0.2 * inch), onLaterPages=base.side_bar_callback(dark, width=0.2 * inch))
