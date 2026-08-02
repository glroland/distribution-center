import io
import random

import numpy as np
import pytest
from PIL import Image

from src.stickers import (
    HORIZONTAL_EXCLUSION_DEGREES,
    InvalidSkuError,
    _random_rotation_angle,
    _validate_sku,
    generate_sticker_image,
)

_SIZE_KWARGS = dict(min_width=200, max_width=260, min_height=150, max_height=190)


def test_generate_sticker_image_returns_valid_image() -> None:
    data = generate_sticker_image("sku-1001", **_SIZE_KWARGS)
    img = Image.open(io.BytesIO(data))
    img.verify()


def test_generate_sticker_image_size_is_within_range() -> None:
    for _ in range(10):
        data = generate_sticker_image("sku-1001", **_SIZE_KWARGS)
        img = Image.open(io.BytesIO(data))
        assert 200 <= img.width <= 260
        assert 150 <= img.height <= 190


def test_generate_sticker_image_rejects_blank_sku() -> None:
    with pytest.raises(InvalidSkuError):
        generate_sticker_image("   ", **_SIZE_KWARGS)


def test_validate_sku_forces_upper_case() -> None:
    assert _validate_sku(" sku-1001 ") == "SKU-1001"


def test_generate_sticker_image_bw_mode_has_no_color() -> None:
    # PNG (lossless) so JPEG chroma subsampling can't introduce spurious per-channel drift.
    data = generate_sticker_image("sku-1001", color_mode="bw", image_format="png", **_SIZE_KWARGS)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    r, g, b = img.split()

    assert np.array_equal(np.array(r), np.array(g))
    assert np.array_equal(np.array(g), np.array(b))


def test_generate_sticker_image_color_mode_can_have_color() -> None:
    # Not every draw has to differ per-channel, but across many random surfaces at least one should.
    found_color = False
    for _ in range(15):
        data = generate_sticker_image("sku-1001", color_mode="color", **_SIZE_KWARGS)
        img = Image.open(io.BytesIO(data)).convert("RGB")
        arr = np.array(img)
        if not (arr[..., 0] == arr[..., 1]).all():
            found_color = True
            break
    assert found_color


def test_generate_sticker_image_png_format() -> None:
    data = generate_sticker_image("sku-1001", image_format="png", **_SIZE_KWARGS)
    img = Image.open(io.BytesIO(data))
    assert img.format == "PNG"


def test_generate_sticker_image_jpg_format() -> None:
    data = generate_sticker_image("sku-1001", image_format="jpg", **_SIZE_KWARGS)
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG"


def test_random_rotation_angle_never_lands_on_horizontal() -> None:
    band = HORIZONTAL_EXCLUSION_DEGREES
    rng = random.Random(1234)
    for _ in range(5000):
        angle = _random_rotation_angle(rng)
        assert band <= angle <= 360 - band
        assert not (180 - band < angle < 180 + band)
