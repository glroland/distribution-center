"""Summit Manufacturing -- boxed industrial header, steel-gray grid table."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from src.models import Company, PurchaseOrder
from src.templates import base

COMPANY = Company(
    name="Summit Manufacturing",
    address_lines=["900 Alloy Street", "Pittsburgh, PA 15222"],
    phone="(412) 555-0177",
    email="procurement@summitmfg.com",
    accent_color="#37474f",
    font="Helvetica-Bold",
)

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


def render(po: PurchaseOrder, output_path: str) -> None:
    doc = base.make_doc(output_path)
    story = []

    accent = colors.HexColor(COMPANY.accent_color)

    header = Table(
        [
            [
                Paragraph(COMPANY.name.upper(), base.style("company", FONT_BOLD, 16, text_color=colors.white)),
                Paragraph("PURCHASE<br/>ORDER", base.style("title", FONT_BOLD, 14, alignment=2, text_color=colors.white)),
            ]
        ],
        colWidths=[4.4 * inch, 2.0 * inch],
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), accent),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ("LEFTPADDING", (0, 0), (0, -1), 10),
                ("RIGHTPADDING", (1, 0), (1, -1), 10),
            ]
        )
    )
    story.append(header)
    story.append(
        Paragraph(
            f"{COMPANY.address_lines[0]}, {COMPANY.address_lines[1]}  |  {COMPANY.phone}  |  {COMPANY.email}",
            base.style("company_sub", FONT, 8.5, text_color=colors.HexColor("#555555"), space_after=0),
        )
    )
    story.append(Spacer(1, 0.25 * inch))

    info = Table(
        [
            [
                base.address_block("VENDOR", [po.vendor.name, *po.vendor.address_lines, po.vendor.phone], FONT, FONT_BOLD, title_color=accent),
                base.address_block("SHIP TO", po.ship_to, FONT, FONT_BOLD, title_color=accent),
                base.po_meta_block(po, FONT, FONT_BOLD, label_color=colors.HexColor("#666666")),
            ]
        ],
        colWidths=[2.4 * inch, 2.0 * inch, 2.0 * inch],
    )
    info.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cfd8dc")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cfd8dc")),
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
            "All shipments must include a packing slip referencing the PO number. Non-conforming material will be "
            "returned at vendor's expense.",
            base.style("footer", FONT, 8, text_color=colors.grey),
        )
    )

    doc.build(story)
