import io
import math
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
    generate_sticker_sample,
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


# Comfortably larger than any generated sticker (font size <= 52, ~8-char SKU text) so the
# "shrink the rotated sticker to fit the canvas" branch in `_composite` never triggers here -
# that keeps the corner-geometry checks below exact rather than fuzzed by an extra scale factor.
_ROOMY_SIZE_KWARGS = dict(min_width=1400, max_width=1600, min_height=1000, max_height=1200)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def test_generate_sticker_sample_corners_form_the_sticker_rectangle() -> None:
    for seed in range(25):
        _, metadata = generate_sticker_sample("sku-1001", seed=seed, **_ROOMY_SIZE_KWARGS)
        tl, tr, br, bl = metadata.corners_xy

        # Opposite sides equal in length...
        assert _dist(tl, tr) == pytest.approx(_dist(bl, br), rel=1e-6)
        assert _dist(tl, bl) == pytest.approx(_dist(tr, br), rel=1e-6)
        # ...and matching the sticker's own (unrotated) width/height exactly, since rotation is
        # a rigid transform and this canvas is roomy enough that no extra scaling was applied.
        assert _dist(tl, tr) == pytest.approx(metadata.sticker_width, abs=0.5)
        assert _dist(tl, bl) == pytest.approx(metadata.sticker_height, abs=0.5)

        # Adjacent sides are perpendicular (it's a rectangle, not a sheared quadrilateral).
        edge_top = (tr[0] - tl[0], tr[1] - tl[1])
        edge_left = (bl[0] - tl[0], bl[1] - tl[1])
        dot = edge_top[0] * edge_left[0] + edge_top[1] * edge_left[1]
        assert dot == pytest.approx(0, abs=1e-6)


def test_generate_sticker_sample_corners_stay_within_canvas() -> None:
    for seed in range(25):
        _, metadata = generate_sticker_sample("sku-1002", seed=seed, **_ROOMY_SIZE_KWARGS)
        for corner_x, corner_y in metadata.corners_xy:
            assert -1 <= corner_x <= metadata.canvas_width + 1
            assert -1 <= corner_y <= metadata.canvas_height + 1


def test_generate_sticker_sample_matches_generate_sticker_image() -> None:
    # generate_sticker_image() must remain byte-for-byte identical to before this refactor -
    # it's a thin wrapper around generate_sticker_sample() now, same seeded RNG sequence.
    kwargs = dict(seed=42, **_ROOMY_SIZE_KWARGS)
    image_bytes, _metadata = generate_sticker_sample("sku-1003", **kwargs)
    assert generate_sticker_image("sku-1003", **kwargs) == image_bytes
