"""Pacific Tech Supply -- modern dark-slate banner, teal accent, minimal rules."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from src.models import Company, PurchaseOrder
from src.templates import base

COMPANY = Company(
    name="Pacific Tech Supply",
    address_lines=["500 Innovation Way, Floor 12", "San Jose, CA 95110"],
    phone="(408) 555-0171",
    email="orders@pacifictechsupply.com",
    accent_color="#0f2a43",
    font="Helvetica",
)

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
TEAL = "#2dd4bf"


def render(po: PurchaseOrder, output_path: str) -> None:
    doc = base.make_doc(output_path, top_margin=0.35 * inch)
    story = []

    story.append(Paragraph(COMPANY.name, base.style("company", FONT_BOLD, 20, text_color=colors.white)))
    story.append(
        Paragraph(
            f"{COMPANY.address_lines[0]}, {COMPANY.address_lines[1]} &nbsp;|&nbsp; {COMPANY.email}",
            base.style("company_sub", FONT, 9, text_color=colors.HexColor(TEAL)),
        )
    )
    story.append(Spacer(1, 0.5 * inch))

    story.append(Paragraph("Purchase Order", base.style("title", FONT_BOLD, 14, text_color=colors.HexColor(COMPANY.accent_color))))
    story.append(Spacer(1, 0.15 * inch))

    info = Table(
        [
            [
                base.address_block("Vendor", [po.vendor.name, *po.vendor.address_lines, po.vendor.phone], FONT, FONT_BOLD, title_color=colors.HexColor("#0f766e")),
                base.address_block("Ship To", po.ship_to, FONT, FONT_BOLD, title_color=colors.HexColor("#0f766e")),
                base.po_meta_block(po, FONT, FONT_BOLD, label_color=colors.HexColor("#6b7280")),
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
            header_fg=colors.HexColor(COMPANY.accent_color),
            grid_style="lines",
            accent_color=TEAL,
        )
    )
    story.append(Spacer(1, 0.18 * inch))
    story.append(base.totals_table(po, font_name=FONT, bold_font_name=FONT_BOLD, accent_color=colors.HexColor("#0f766e")))
    story.append(Spacer(1, 0.4 * inch))
    story.append(
        Paragraph(
            "Digital delivery confirmations preferred. Please email tracking information to the address above upon shipment.",
            base.style("footer", FONT, 8, text_color=colors.grey),
        )
    )

    banner = base.banner_callback(COMPANY.accent_color, height=1.05 * inch)
    doc.build(story, onFirstPage=banner, onLaterPages=banner)
