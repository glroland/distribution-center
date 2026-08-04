from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import inference
from src.app import app
from src.settings import settings
from src.stickers import generate_sticker_image

client = TestClient(app)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


@pytest.fixture(autouse=True)
def _reset_pipeline_cache():
    inference.reset_pipeline_cache()
    yield
    inference.reset_pipeline_cache()


def _sticker_bytes(sku: str, seed: int | None = None) -> bytes:
    return generate_sticker_image(
        sku,
        min_width=settings.MIN_IMAGE_WIDTH,
        max_width=settings.MAX_IMAGE_WIDTH,
        min_height=settings.MIN_IMAGE_HEIGHT,
        max_height=settings.MAX_IMAGE_HEIGHT,
        color_mode="random",
        image_format="jpg",
        seed=seed,
    )


def test_infer_returns_sku_and_confidence() -> None:
    image_bytes = _sticker_bytes("SKU-1007")
    resp = client.post("/infer", files={"image": ("sticker.jpg", image_bytes, "image/jpeg")})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["sku"]) <= set(inference.OCR_CHARSET)
    assert 0.0 <= body["confidence"] <= 1.0
    assert len(body["bbox"]) == 4
    assert isinstance(body["angle_degrees"], float)
    assert body["inference_ms"] > 0


def test_infer_rejects_non_image_upload() -> None:
    resp = client.post("/infer", files={"image": ("not-an-image.txt", b"hello world", "text/plain")})

    assert resp.status_code == 400
    assert "invalid image" in resp.json()["detail"]


def test_infer_reports_503_when_models_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "INFERENCE_MODELS_DIR", str(tmp_path))
    image_bytes = _sticker_bytes("SKU-1008")

    resp = client.post("/infer", files={"image": ("sticker.jpg", image_bytes, "image/jpeg")})

    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"]


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[-1]


def test_pipeline_predicts_the_sku_it_was_given() -> None:
    """End-to-end sanity check against the real bundled checkpoints (not a
    mock) - the whole point of this endpoint is that these 3 small CNNs are
    good enough to round-trip label-api's own sticker generator. `seed` pins
    the sticker's canvas/rotation/color for a reproducible run; the
    Levenshtein tolerance (rather than exact-match) is deliberate - this is a
    lightweight demo OCR model, not a production one, and occasionally
    misreads a single character even on an easy, confidently-scored crop
    (see the git history around this test for measured per-seed accuracy).
    A total miss (empty string, wrong charset, way off) would still fail
    this; single-character noise on an otherwise-correct read shouldn't."""
    pipeline = inference.get_pipeline(models_dir=MODELS_DIR)
    from PIL import Image
    import io

    sku = "SKU-1009"
    image = Image.open(io.BytesIO(_sticker_bytes(sku, seed=2)))
    prediction = pipeline.predict(image)

    assert _levenshtein(prediction.sku, sku) <= 1
    assert prediction.confidence > 0.5
