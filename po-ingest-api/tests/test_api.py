import io

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from src.app import app

client = TestClient(app)


def _sample_pdf_bytes() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "Purchase Order 1001")
    c.drawString(100, 730, "Vendor: Acme Industrial Supply")
    c.save()
    return buf.getvalue()


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_convert_pdf() -> None:
    resp = client.post(
        "/convert",
        files={"file": ("sample.pdf", _sample_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200

    body = resp.json()
    assert body["filename"] == "sample.pdf"
    assert "Purchase Order" in body["markdown"]
    assert isinstance(body["document"], dict)
    assert body["document"]


def test_convert_rejects_non_pdf() -> None:
    resp = client.post(
        "/convert",
        files={"file": ("sample.txt", b"not a pdf", "text/plain")},
    )
    assert resp.status_code == 400
