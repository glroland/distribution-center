import asyncio
import base64
import io

from reportlab.pdfgen import canvas

from src.mcp_server import mcp_server


def _sample_pdf_bytes() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "Purchase Order 1001")
    c.save()
    return buf.getvalue()


def test_convert_pdf_to_markdown_tool() -> None:
    pdf_b64 = base64.b64encode(_sample_pdf_bytes()).decode()
    result = asyncio.run(
        mcp_server.call_tool(
            "convert_pdf_to_markdown", {"pdf_base64": pdf_b64, "filename": "sample.pdf"}
        )
    )
    content, _structured = result
    text = "".join(part.text for part in content if hasattr(part, "text"))
    assert "Purchase Order" in text
