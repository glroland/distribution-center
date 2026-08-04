import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.app import app
from src.settings import settings

client = TestClient(app)


@pytest.fixture(autouse=True)
def _bulk_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BULK_OUTPUT_DIR", str(tmp_path))
    yield


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_generate_sticker() -> None:
    resp = client.get("/stickers/sku-1001")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(resp.content))
    img.verify()


def test_generate_sticker_png() -> None:
    resp = client.get("/stickers/sku-1001", params={"image_format": "png"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_generate_sticker_rejects_blank_sku() -> None:
    resp = client.get("/stickers/%20%20")
    assert resp.status_code == 400


def test_generate_sticker_invalid_color_mode() -> None:
    resp = client.get("/stickers/sku-1001", params={"color_mode": "sepia"})
    assert resp.status_code == 422


def test_generate_stickers_bulk() -> None:
    resp = client.post(
        "/stickers/bulk",
        json={
            "items": [
                {"sku": "SKU-1001", "quantity": 2},
                {"sku": "SKU-1002", "quantity": 1},
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert len(zf.namelist()) == 3


def test_generate_stickers_bulk_with_manifest() -> None:
    resp = client.post(
        "/stickers/bulk",
        json={
            "items": [{"sku": "SKU-1001", "quantity": 2}],
            "include_manifest": True,
        },
    )
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert "manifest.jsonl" in names
        assert len(names) == 3  # 2 images + manifest


def test_generate_stickers_bulk_rejects_empty_items() -> None:
    resp = client.post("/stickers/bulk", json={"items": []})
    assert resp.status_code == 422


def test_generate_stickers_bulk_rejects_blank_sku() -> None:
    resp = client.post("/stickers/bulk", json={"items": [{"sku": "  ", "quantity": 1}]})
    assert resp.status_code == 422
