"""Golden State Foods -- warm red/gold banner, zebra table."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from src.models import Company, PurchaseOrder
from src.templates import base

COMPANY = Company(
    name="Golden State Foods Distribution",
    address_lines=["770 Orchard Grove Ave", "Fresno, CA 93706"],
    phone="(559) 555-0129",
    email="orders@goldenstatefoods.com",
    accent_color="#b5321f",
    font="Helvetica-Bold",
)

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
GOLD = "#d4a017"


def render(po: PurchaseOrder, output_path: str) -> None:
    doc = base.make_doc(output_path, top_margin=0.4 * inch)
    story = []

    story.append(Paragraph(COMPANY.name.upper(), base.style("company", FONT_BOLD, 19, text_color=colors.white)))
    story.append(
        Paragraph(
            f"{COMPANY.address_lines[0]}, {COMPANY.address_lines[1]} &nbsp;|&nbsp; {COMPANY.phone}",
            base.style("company_sub", FONT, 9, text_color=colors.HexColor("#f8d99b")),
        )
    )
    story.append(Spacer(1, 0.55 * inch))

    story.append(Paragraph("PURCHASE ORDER", base.style("title", FONT_BOLD, 16, text_color=colors.HexColor(COMPANY.accent_color))))
    story.append(Spacer(1, 0.15 * inch))

    info = Table(
        [
            [
                base.address_block("VENDOR", [po.vendor.name, *po.vendor.address_lines, po.vendor.phone], FONT, FONT_BOLD, title_color=colors.HexColor(GOLD)),
                base.address_block("SHIP TO", po.ship_to, FONT, FONT_BOLD, title_color=colors.HexColor(GOLD)),
                base.po_meta_block(po, FONT, FONT_BOLD, label_color=colors.HexColor("#8a4a3a")),
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
            grid_style="zebra",
            accent_color=GOLD,
            zebra_color="#fdf1e0",
        )
    )
    story.append(Spacer(1, 0.18 * inch))
    story.append(base.totals_table(po, font_name=FONT, bold_font_name=FONT_BOLD, accent_color=colors.HexColor(COMPANY.accent_color)))
    story.append(Spacer(1, 0.4 * inch))
    story.append(
        Paragraph(
            "Perishable goods must arrive within the cold-chain window noted on the delivery appointment. "
            "Reject damaged pallets at receiving dock.",
            base.style("footer", FONT, 8, text_color=colors.grey),
        )
    )

    banner = base.banner_callback(COMPANY.accent_color)
    doc.build(story, onFirstPage=banner, onLaterPages=banner)
