"""Shared ReportLab helpers reused by every company template.

Each template still owns its own page layout and calls into these helpers with
different fonts/colors/table styles so the 12 templates stay visually distinct
while avoiding copy-pasted boilerplate.
"""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from src.models import PurchaseOrder

PAGE_SIZE = letter
PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 0.6 * inch
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN


def fmt_currency(value: float) -> str:
    return f"${value:,.2f}"


def style(
    name: str,
    font_name: str = "Helvetica",
    font_size: int = 10,
    leading: int | None = None,
    text_color: str | colors.Color = colors.black,
    alignment: int = 0,
    space_after: int = 0,
) -> ParagraphStyle:
    return ParagraphStyle(
        name=name,
        fontName=font_name,
        fontSize=font_size,
        leading=leading or font_size * 1.25,
        textColor=text_color,
        alignment=alignment,
        spaceAfter=space_after,
    )


def line_items_table(
    po: PurchaseOrder,
    font_name: str = "Helvetica",
    header_font_name: str | None = None,
    header_bg: str | colors.Color = "#1a3c6e",
    header_fg: str | colors.Color = colors.white,
    grid_style: str = "grid",
    accent_color: str | colors.Color = "#1a3c6e",
    zebra_color: str | colors.Color = "#f0f0f0",
    font_size: int = 9,
) -> Table:
    """Builds the SKU/description/qty/price/total table.

    grid_style: "grid" (full ruled grid), "zebra" (alternating row shading,
    no verticals), or "lines" (horizontal rules only, minimal look).
    """
    header_font_name = header_font_name or font_name

    header = ["SKU", "Description", "Qty", "Unit Price", "Line Total"]
    rows = [header]
    for item in po.line_items:
        rows.append(
            [
                item.sku,
                item.description,
                str(item.quantity),
                fmt_currency(item.unit_price),
                fmt_currency(item.line_total),
            ]
        )

    col_widths = [
        0.16 * CONTENT_WIDTH,
        0.44 * CONTENT_WIDTH,
        0.10 * CONTENT_WIDTH,
        0.15 * CONTENT_WIDTH,
        0.15 * CONTENT_WIDTH,
    ]

    table = Table(rows, colWidths=col_widths, repeatRows=1)

    commands = [
        ("FONTNAME", (0, 0), (-1, 0), header_font_name),
        ("FONTNAME", (0, 1), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg) if isinstance(header_bg, str) else header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), header_fg),
        ("ALIGN", (2, 0), (4, -1), "RIGHT"),
        ("ALIGN", (0, 0), (1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]

    accent = colors.HexColor(accent_color) if isinstance(accent_color, str) else accent_color

    if grid_style == "grid":
        commands.append(("GRID", (0, 0), (-1, -1), 0.5, colors.grey))
    elif grid_style == "zebra":
        zebra = colors.HexColor(zebra_color) if isinstance(zebra_color, str) else zebra_color
        for row_idx in range(1, len(rows)):
            if row_idx % 2 == 0:
                commands.append(("BACKGROUND", (0, row_idx), (-1, row_idx), zebra))
        commands.append(("LINEBELOW", (0, 0), (-1, 0), 1, accent))
        commands.append(("LINEBELOW", (0, -1), (-1, -1), 1, accent))
    elif grid_style == "lines":
        commands.append(("LINEBELOW", (0, 0), (-1, 0), 1.2, accent))
        for row_idx in range(1, len(rows)):
            commands.append(("LINEBELOW", (0, row_idx), (-1, row_idx), 0.4, colors.lightgrey))

    table.setStyle(TableStyle(commands))
    return table


def make_doc(
    output_path: str,
    top_margin: float = MARGIN,
    bottom_margin: float = MARGIN,
    left_margin: float = MARGIN,
    right_margin: float = MARGIN,
) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        str(output_path),
        pagesize=PAGE_SIZE,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
        leftMargin=left_margin,
        rightMargin=right_margin,
        title="Purchase Order",
    )


def banner_callback(color: str | colors.Color, height: float = 1.15 * inch):
    """Returns an onFirstPage/onLaterPages callback that paints a full-width
    color banner across the top of the page, behind the flowable content."""

    fill = colors.HexColor(color) if isinstance(color, str) else color

    def _draw(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(fill)
        canvas.rect(0, PAGE_HEIGHT - height, PAGE_WIDTH, height, fill=1, stroke=0)
        canvas.restoreState()

    return _draw


def side_bar_callback(color: str | colors.Color, width: float = 0.22 * inch):
    """Returns a callback that paints a solid color bar down the left edge."""

    fill = colors.HexColor(color) if isinstance(color, str) else color

    def _draw(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(fill)
        canvas.rect(0, 0, width, PAGE_HEIGHT, fill=1, stroke=0)
        canvas.restoreState()

    return _draw


def bordered_page_callback(color: str | colors.Color, inset: float = 0.35 * inch, width: float = 1.2):
    """Returns a callback that draws a rectangular border around the page --
    used for formal/government-style letterheads."""

    stroke = colors.HexColor(color) if isinstance(color, str) else color

    def _draw(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(stroke)
        canvas.setLineWidth(width)
        canvas.rect(inset, inset, PAGE_WIDTH - 2 * inset, PAGE_HEIGHT - 2 * inset, fill=0, stroke=1)
        canvas.restoreState()

    return _draw


def address_block(
    title: str,
    lines: list[str],
    font_name: str = "Helvetica",
    bold_font_name: str | None = None,
    font_size: int = 9,
    title_color: str | colors.Color = colors.black,
) -> list:
    bold_font_name = bold_font_name or font_name
    color = colors.HexColor(title_color) if isinstance(title_color, str) else title_color
    flows = [
        Paragraph(
            title,
            style("addr_title", bold_font_name, font_size + 1, text_color=color, space_after=3),
        )
    ]
    for line in lines:
        flows.append(Paragraph(line, style("addr_line", font_name, font_size, space_after=2)))
    return flows


def po_meta_block(
    po: PurchaseOrder,
    font_name: str = "Helvetica",
    bold_font_name: str | None = None,
    font_size: int = 9,
    label_color: str | colors.Color = colors.grey,
) -> Table:
    bold_font_name = bold_font_name or font_name
    rows = [
        ["PO Number:", po.po_number],
        ["Date:", po.issue_date],
        ["Payment Terms:", po.payment_terms],
        ["Buyer Contact:", po.buyer_contact],
    ]
    table = Table(rows, colWidths=[1.3 * inch, 1.9 * inch])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), font_name),
                ("FONTNAME", (1, 0), (1, -1), bold_font_name),
                ("FONTSIZE", (0, 0), (-1, -1), font_size),
                ("TEXTCOLOR", (0, 0), (0, -1), label_color),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def totals_table(
    po: PurchaseOrder,
    font_name: str = "Helvetica",
    bold_font_name: str | None = None,
    accent_color: str | colors.Color = "#1a3c6e",
    font_size: int = 10,
) -> Table:
    bold_font_name = bold_font_name or font_name
    accent = colors.HexColor(accent_color) if isinstance(accent_color, str) else accent_color

    rows = [["Subtotal", fmt_currency(po.subtotal)]]
    if po.tax_rate:
        rows.append([f"Tax ({po.tax_rate * 100:.1f}%)", fmt_currency(po.tax)])
    rows.append(["Total", fmt_currency(po.total)])

    col_widths = [0.28 * CONTENT_WIDTH, 0.15 * CONTENT_WIDTH]
    table = Table(rows, colWidths=col_widths, hAlign="RIGHT")

    last_row = len(rows) - 1
    commands = [
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTNAME", (0, last_row), (-1, last_row), bold_font_name),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("FONTSIZE", (0, last_row), (-1, last_row), font_size + 1),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, last_row), (-1, last_row), 1, accent),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TEXTCOLOR", (0, last_row), (-1, last_row), accent),
    ]
    table.setStyle(TableStyle(commands))
    return table
