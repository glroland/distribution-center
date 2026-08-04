import math
import random

import numpy as np
from PIL import Image

from src import geometry


def _make_tilted_rectangle(w: float, h: float, angle_degrees: float, center: tuple[float, float]):
    """A synthetic rectangle (independent of label-api) tilted by
    `angle_degrees` per `rotate_points`'s convention, so `corners_to_angle_degrees`
    can be checked against a known-good input rather than only self-consistently."""
    # rotate_points(X, a) is defined to *undo* a tilt of `a` (that's what
    # rotate_upright relies on) - so building a shape tilted by +angle_degrees
    # means applying rotate_points with the opposite sign.
    corners_rel = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    rotated_rel = geometry.rotate_points(corners_rel, -angle_degrees, center=(0, 0))
    cx, cy = center
    return [(x + cx, y + cy) for x, y in rotated_rel]


def test_corners_to_angle_degrees_recovers_known_tilt():
    for angle in [10, 45, 90, 123.4, 200, 300, 359]:
        corners = _make_tilted_rectangle(120, 60, angle, center=(0, 0))
        recovered = geometry.corners_to_angle_degrees(corners)
        assert recovered == pytest_approx(angle % 360)


def test_rotate_upright_straightens_a_tilted_rectangle():
    """The real correctness proof for `rotate_upright`: apply it (in point-space,
    via `rotate_points`, its documented equivalent) to a tilted rectangle and
    confirm the top edge becomes exactly horizontal."""
    for angle in [10, 45, 90, 123.4, 200, 300, 359]:
        corners = _make_tilted_rectangle(120, 60, angle, center=(50, 50))
        recovered_angle = geometry.corners_to_angle_degrees(corners)
        uprighted = geometry.rotate_points(corners, recovered_angle, center=(50, 50))
        top_left, top_right, bottom_right, bottom_left = uprighted

        assert top_left[1] == pytest_approx(top_right[1])  # top edge horizontal
        assert bottom_left[1] == pytest_approx(bottom_right[1])  # bottom edge horizontal
        assert top_left[1] < bottom_left[1]  # right-side-up, not upside-down


def test_corners_to_bbox_matches_min_max():
    corners = [(1, 5), (10, 2), (8, 20), (-3, 9)]
    assert geometry.corners_to_bbox(corners) == (-3, 2, 10, 20)


def test_pad_bbox_clips_to_canvas():
    padded = geometry.pad_bbox((0, 0, 10, 10), pad_frac=1.0, canvas_size=(15, 15))
    assert padded == (0, 0, 15, 15)


def test_normalize_denormalize_bbox_roundtrip():
    bbox = (10, 20, 100, 150)
    canvas = (200, 300)
    norm = geometry.normalize_bbox(bbox, canvas)
    assert all(0 <= v <= 1 for v in norm)
    back = geometry.denormalize_bbox(norm, canvas)
    for a, b in zip(bbox, back):
        assert a == pytest_approx(b)


def test_jitter_bbox_stays_within_canvas_and_roughly_near_original():
    rng = random.Random(0)
    bbox = (100, 100, 200, 180)
    canvas = (300, 300)
    for _ in range(200):
        jittered = geometry.jitter_bbox(bbox, canvas, rng=rng)
        xmin, ymin, xmax, ymax = jittered
        assert 0 <= xmin < xmax <= canvas[0]
        assert 0 <= ymin < ymax <= canvas[1]
        # Not wildly displaced - still overlapping the original box's neighborhood.
        assert xmin < 250 and xmax > 50
        assert ymin < 230 and ymax > 50


def test_angle_sin_cos_roundtrip():
    rng = random.Random(0)
    for _ in range(50):
        angle = rng.uniform(0, 360)
        sin_v, cos_v = geometry.angle_to_sin_cos(angle)
        recovered = geometry.sin_cos_to_angle(sin_v, cos_v)
        assert recovered == pytest_approx(angle % 360)


def pytest_approx(value, abs=1e-6):
    import pytest

    return pytest.approx(value, abs=abs)


def test_rotate_upright_on_a_real_image_matches_rotate_points():
    """Closes the loop on real PIL.Image.rotate() (not just the point-space
    model of it): draw a white rectangle tilted by a known angle onto a
    padded canvas the same way label-api does (small unrotated
    rect -> rotate(expand=True) -> paste), run it through `rotate_upright`,
    and confirm the white region's actual pixel bounding box in the result
    is axis-aligned and sized like the original unrotated rectangle."""
    w, h, angle = 80, 40, 37.0
    rect = Image.new("L", (w, h), 255)
    rotated = rect.rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=0)

    pad = 40
    canvas = Image.new("L", (rotated.width + 2 * pad, rotated.height + 2 * pad), 0)
    canvas.paste(rotated, (pad, pad))

    # corners_to_angle_degrees expects [TL, TR, BR, BL] of the *unrotated* rect mapped through
    # the same rotation - reuse rotate_points (rotate_upright's point-space model) to build them,
    # exactly mirroring label-api's own corner construction.
    # `rotate_points(X, a)` is defined to mirror `Image.rotate(a, expand=False)`'s own point
    # mapping (see geometry.rotate_upright's docstring, and label-api's
    # `_rotated_sticker_corners`, which uses this identical formula and is verified against
    # real generated images in that service's test suite) - so the *same* +angle used above to
    # build `rotated` via `rect.rotate(angle, ...)` is what maps `rect`'s own corners into
    # `rotated`'s frame here.
    corners_rel = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    rotated_rel = geometry.rotate_points(corners_rel, angle, center=(0, 0))
    rc_x, rc_y = rotated.width / 2 + pad, rotated.height / 2 + pad
    corners = [(x + rc_x, y + rc_y) for x, y in rotated_rel]
    recovered_angle = geometry.corners_to_angle_degrees(corners)

    upright = geometry.rotate_upright(canvas, recovered_angle)
    arr = np.array(upright)
    ys, xs = np.where(arr > 127)
    bbox_w, bbox_h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1

    # Axis-aligned and within a pixel or two of the original rectangle's own size (rotate/resample
    # softens edges slightly, so allow a small tolerance rather than demanding an exact match).
    assert abs(bbox_w - w) <= 2
    assert abs(bbox_h - h) <= 2
