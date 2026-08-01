"""Meridian Healthcare Supply -- clinical blue/white banner, zebra table."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from src.models import Company, PurchaseOrder
from src.templates import base

COMPANY = Company(
    name="Meridian Healthcare Supply",
    address_lines=["1500 Wellness Parkway", "Columbus, OH 43215"],
    phone="(614) 555-0184",
    email="procurement@meridianhealthcaresupply.com",
    accent_color="#2f6690",
    font="Helvetica",
)

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


def render(po: PurchaseOrder, output_path: str) -> None:
    doc = base.make_doc(output_path, top_margin=0.35 * inch)
    story = []

    story.append(Paragraph(COMPANY.name, base.style("company", FONT_BOLD, 18, text_color=colors.white)))
    story.append(
        Paragraph(
            f"{COMPANY.address_lines[0]}, {COMPANY.address_lines[1]} &nbsp;|&nbsp; {COMPANY.phone} &nbsp;|&nbsp; {COMPANY.email}",
            base.style("company_sub", FONT, 8.5, text_color=colors.white),
        )
    )
    story.append(Spacer(1, 0.5 * inch))

    story.append(Paragraph("Purchase Order", base.style("title", FONT_BOLD, 14, text_color=colors.HexColor(COMPANY.accent_color))))
    story.append(Spacer(1, 0.15 * inch))

    info = Table(
        [
            [
                base.address_block("Vendor", [po.vendor.name, *po.vendor.address_lines, po.vendor.phone], FONT, FONT_BOLD),
                base.address_block("Ship To", po.ship_to, FONT, FONT_BOLD),
                base.po_meta_block(po, FONT, FONT_BOLD, label_color=colors.HexColor("#5c86a3")),
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
            accent_color=COMPANY.accent_color,
            zebra_color="#eaf3fa",
        )
    )
    story.append(Spacer(1, 0.18 * inch))
    story.append(base.totals_table(po, font_name=FONT, bold_font_name=FONT_BOLD, accent_color=COMPANY.accent_color))
    story.append(Spacer(1, 0.4 * inch))
    story.append(
        Paragraph(
            "All items must ship with lot numbers and expiration dates where applicable. Cold-chain items require "
            "temperature-monitored transport.",
            base.style("footer", FONT, 8, text_color=colors.grey),
        )
    )

    banner = base.banner_callback(COMPANY.accent_color, height=1.05 * inch)
    doc.build(story, onFirstPage=banner, onLaterPages=banner)
