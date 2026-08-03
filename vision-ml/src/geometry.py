"""Geometry shared between training-target construction and inference-time
reconstruction. Single source of truth so the two never drift apart: every
model's training target and every model's inference-time crop/rotate step
calls the same functions here.

Corners are always `[top_left, top_right, bottom_right, bottom_left]` of the
*unrotated* sticker rectangle, mapped through whatever rotation
label-generator-api actually applied - see that service's
`_rotated_sticker_corners` for how they're produced. `corners_to_angle_degrees`
defines its own angle convention (the tilt of the top edge relative to
horizontal); it does not need to, and does not, match
label-generator-api's own `rotation_angle_degrees` field, which uses PIL's
internal rotate() sign convention instead. Don't mix the two.
"""

from __future__ import annotations

import math
import random
from typing import Sequence

from PIL import Image

Point = tuple[float, float]
BBox = tuple[float, float, float, float]  # xmin, ymin, xmax, ymax


def corners_to_bbox(corners: Sequence[Point]) -> BBox:
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def corners_to_angle_degrees(corners: Sequence[Point]) -> float:
    """Angle of the top edge (corner 0 -> corner 1, i.e. top_left -> top_right)
    relative to horizontal, in [0, 360). This is the value `rotate_upright`
    expects and what `OrientationNet` is trained to predict."""
    (x0, y0), (x1, y1) = corners[0], corners[1]
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360


def pad_bbox(bbox: BBox, pad_frac: float, canvas_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Expand `bbox` by `pad_frac` of its longer side on every side, clipped to the canvas.

    The unpadded bbox is already the axis-aligned box of a *tilted* rectangle
    (bigger than the sticker's own footprint), so this padding is slack on
    top of that - generous enough that `rotate_upright` on the resulting crop
    doesn't clip the sticker for any rotation angle.
    """
    xmin, ymin, xmax, ymax = bbox
    pad = pad_frac * max(xmax - xmin, ymax - ymin)
    cw, ch = canvas_size
    return (
        max(0, int(xmin - pad)),
        max(0, int(ymin - pad)),
        min(cw, int(xmax + pad)),
        min(ch, int(ymax + pad)),
    )


def jitter_bbox(
    bbox: BBox,
    canvas_size: tuple[int, int],
    position_frac: float = 0.04,
    scale_frac: float = 0.05,
    rng: random.Random | None = None,
) -> BBox:
    """Perturb `bbox`'s position and scale to roughly resemble what an
    imperfect upstream localizer produces (`LocalizerNet` lands around 0.7
    mean IoU - not pixel-perfect). Training `OrientationDataset`/`OcrDataset`
    only against pixel-perfect ground-truth boxes leaves those stages
    fragile to that real imprecision: chaining all 3 stages together in
    `04_end_to_end_pipeline.ipynb` initially scored far worse end-to-end than
    each stage's isolated accuracy suggested it should, because the crops
    those stages saw at inference never looked like the ones they trained
    on. Applying this at both train *and* val time keeps val metrics
    representative of real chained-inference conditions.
    """
    rng = rng or random
    xmin, ymin, xmax, ymax = bbox
    width, height = xmax - xmin, ymax - ymin
    dx = rng.gauss(0, position_frac * width)
    dy = rng.gauss(0, position_frac * height)
    scale = max(0.5, 1 + rng.gauss(0, scale_frac))

    cx, cy = xmin + width / 2 + dx, ymin + height / 2 + dy
    half_w, half_h = width * scale / 2, height * scale / 2
    canvas_w, canvas_h = canvas_size
    return (
        min(max(cx - half_w, 0), canvas_w),
        min(max(cy - half_h, 0), canvas_h),
        min(max(cx + half_w, 0), canvas_w),
        min(max(cy + half_h, 0), canvas_h),
    )


def normalize_bbox(bbox: BBox, canvas_size: tuple[int, int]) -> tuple[float, float, float, float]:
    cw, ch = canvas_size
    xmin, ymin, xmax, ymax = bbox
    return (xmin / cw, ymin / ch, xmax / cw, ymax / ch)


def denormalize_bbox(norm_bbox: Sequence[float], canvas_size: tuple[int, int]) -> BBox:
    cw, ch = canvas_size
    x0, y0, x1, y1 = norm_bbox
    return (x0 * cw, y0 * ch, x1 * cw, y1 * ch)


def angle_to_sin_cos(angle_degrees: float) -> tuple[float, float]:
    theta = math.radians(angle_degrees)
    return math.sin(theta), math.cos(theta)


def sin_cos_to_angle(sin_v: float, cos_v: float) -> float:
    return math.degrees(math.atan2(sin_v, cos_v)) % 360


def rotate_upright(image: Image.Image, angle_degrees: float) -> Image.Image:
    """Rotate `image` about its own center, canvas size unchanged, so that
    content whose top edge is tilted by `angle_degrees` (per
    `corners_to_angle_degrees`'s convention) becomes horizontal.

    Derivation: PIL's `Image.rotate(a, expand=False)` maps a point at
    `(x, y)` relative to the image center to
    `(cos(a)*x + sin(a)*y, -sin(a)*x + cos(a)*y)` (from PIL's own rotate()
    source: it builds an output->input affine matrix with `theta=-radians(a)`,
    and this is that matrix's inverse/transpose applied the other way).
    Solving for the `a` that sends a top-edge vector `(dx, dy)` to a
    horizontal one - `-sin(a)*dx + cos(a)*dy == 0` for both endpoints - gives
    `a = atan2(dy, dx)`, exactly `corners_to_angle_degrees`'s definition. So
    passing that value straight into `Image.rotate` is correct, not off by a
    sign - verified in `tests/test_geometry.py` by applying the same matrix
    to the corner points directly and checking the result is axis-aligned.
    """
    return image.rotate(angle_degrees, resample=Image.BICUBIC, expand=False)


def rotate_points(points: Sequence[Point], angle_degrees: float, center: Point) -> list[Point]:
    """Point-space equivalent of `rotate_upright`, for testing the two stay consistent."""
    theta = math.radians(angle_degrees)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    cx, cy = center
    rotated = []
    for x, y in points:
        dx, dy = x - cx, y - cy
        rotated.append((cos_t * dx + sin_t * dy + cx, -sin_t * dx + cos_t * dy + cy))
    return rotated
