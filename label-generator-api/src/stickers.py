"""Synthetic camera-photo generation of white rectangular SKU stickers.

Approximates what a warehouse robot's camera might capture of a label stuck to
a shelf or box: a plain white sticker with printed text, sitting at a random
angle on a noisy, unevenly-lit surface, degraded with blur/grain/downsampling
to read as a cheap camera rather than a clean render. Nothing here is a real
photo or an ML model - it is procedural PIL/numpy compositing.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ColorMode = Literal["color", "bw", "random"]
ImageFormat = Literal["jpg", "png"]


@dataclass(frozen=True)
class StickerMetadata:
    """Ground truth for one generated sample - not returned by the plain
    `/stickers/{sku}` endpoint, but available to callers (e.g. bulk
    generation with `include_manifest=True`) that want to train models
    against this generator's output rather than just look at it."""

    sku: str
    canvas_width: int
    canvas_height: int
    color_mode: Literal["color", "bw"]
    rotation_angle_degrees: float
    # Unrotated sticker rectangle's own size, before it was rotated onto the canvas.
    sticker_width: int
    sticker_height: int
    # The 4 corners of the (rotated) sticker rectangle in final-image pixel
    # coordinates, in order: top-left, top-right, bottom-right, bottom-left
    # of the *unrotated* sticker, mapped through the same rotation this
    # module actually applied.
    corners_xy: list[tuple[float, float]]

# Degrees on either side of a horizontal edge (0 and 180) a sticker may never land in,
# so it always reads as visibly angled rather than perfectly level.
HORIZONTAL_EXCLUSION_DEGREES = 6.0

# Plausible warehouse surfaces the sticker might be photographed against.
_BACKGROUND_PALETTES: list[tuple[int, int, int]] = [
    (176, 158, 130),  # cardboard tan
    (140, 142, 145),  # shelf metal gray
    (90, 94, 98),  # dark steel
    (198, 196, 188),  # concrete floor
    (120, 110, 95),  # wood pallet
]


class InvalidSkuError(ValueError):
    """Raised when a SKU is blank or otherwise unusable as label text."""


def _validate_sku(sku: str) -> str:
    cleaned = sku.strip()
    if not cleaned:
        raise InvalidSkuError("sku must not be blank")
    return cleaned.upper()


def _resolve_color_mode(color_mode: ColorMode, rng: random.Random) -> Literal["color", "bw"]:
    if color_mode == "random":
        return "bw" if rng.random() < 0.3 else "color"
    return color_mode


def _random_rotation_angle(rng: random.Random) -> float:
    """An angle in [0, 360) that never falls within HORIZONTAL_EXCLUSION_DEGREES of 0 or 180."""
    band = HORIZONTAL_EXCLUSION_DEGREES
    if rng.random() < 0.5:
        return rng.uniform(band, 180 - band)
    return rng.uniform(180 + band, 360 - band)


def _make_background(width: int, height: int, rng: random.Random) -> Image.Image:
    base = rng.choice(_BACKGROUND_PALETTES)
    seed = rng.randint(0, 2**31 - 1)
    npy_rng = np.random.default_rng(seed)

    arr = np.full((height, width, 3), base, dtype=float)
    arr += npy_rng.normal(0, 10, (height, width, 3))

    # Soft radial light falloff from a random point, to fake uneven overhead lighting.
    yy, xx = np.mgrid[0:height, 0:width]
    light_x, light_y = rng.uniform(0, width), rng.uniform(0, height)
    max_dist = max((width**2 + height**2) ** 0.5, 1.0)
    dist = np.sqrt((xx - light_x) ** 2 + (yy - light_y) ** 2)
    falloff = 1 - (dist / max_dist)
    arr += (falloff[..., None] * rng.uniform(15, 45))

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _draw_sticker(sku_text: str, rng: random.Random) -> Image.Image:
    font_size = rng.randint(34, 52)
    font = ImageFont.load_default(size=font_size)
    padding_x, padding_y = rng.randint(24, 40), rng.randint(18, 30)

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bbox = probe.textbbox((0, 0), sku_text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    sticker_w = text_w + padding_x * 2
    sticker_h = text_h + padding_y * 2

    sticker = Image.new("RGBA", (sticker_w, sticker_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(sticker)
    draw.rectangle([0, 0, sticker_w - 1, sticker_h - 1], outline=(190, 190, 190, 255), width=2)
    draw.text(
        (sticker_w / 2 - bbox[0] - text_w / 2, sticker_h / 2 - bbox[1] - text_h / 2),
        sku_text,
        font=font,
        fill=(20, 20, 20, 255),
    )
    return sticker


def _rotated_sticker_corners(
    sticker_size: tuple[int, int],
    angle_degrees: float,
    rotated_size: tuple[int, int],
    final_size: tuple[int, int],
    paste_xy: tuple[int, int],
) -> list[tuple[float, float]]:
    """Where the 4 corners of the *unrotated* sticker rectangle land in final
    composed-image pixel coordinates, replicating exactly what
    `sticker.rotate(angle, expand=True)` + the optional uniform resize +
    paste in `_composite` does to those corners.

    PIL's `Image.rotate(angle, expand=True)` builds an output->input affine
    matrix using `theta = -radians(angle)` (see PIL.Image.Image.rotate
    source), which works out to the forward (input->output) map, relative to
    each image's own center, being the rotation matrix
    `[[cos a, sin a], [-sin a, cos a]]` for `a = radians(angle)`. Everything
    below is that relation applied to the sticker's own corners, followed by
    the same resize-to-fit scaling and paste offset `_composite` applies to
    the whole rotated sticker image.
    """
    w, h = sticker_size
    a = math.radians(angle_degrees)
    cos_a, sin_a = math.cos(a), math.sin(a)
    corners_rel = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]

    rot_w, rot_h = rotated_size
    final_w, final_h = final_size
    scale_x, scale_y = final_w / rot_w, final_h / rot_h
    paste_x, paste_y = paste_xy

    corners = []
    for ix, iy in corners_rel:
        rx = cos_a * ix + sin_a * iy
        ry = -sin_a * ix + cos_a * iy
        corners.append(((rx + rot_w / 2) * scale_x + paste_x, (ry + rot_h / 2) * scale_y + paste_y))
    return corners


def _composite(
    background: Image.Image, sticker: Image.Image, rng: random.Random
) -> tuple[Image.Image, float, list[tuple[float, float]]]:
    angle = _random_rotation_angle(rng)
    rotated = sticker.rotate(angle, expand=True, resample=Image.BICUBIC)
    rotated_size = rotated.size

    bg_w, bg_h = background.size
    st_w, st_h = rotated.size
    if st_w >= bg_w or st_h >= bg_h:
        scale = min((bg_w * 0.85) / st_w, (bg_h * 0.85) / st_h)
        rotated = rotated.resize((max(1, int(st_w * scale)), max(1, int(st_h * scale))), Image.LANCZOS)
        st_w, st_h = rotated.size

    x = rng.randint(0, max(bg_w - st_w, 0))
    y = rng.randint(0, max(bg_h - st_h, 0))

    # Faint drop shadow so the sticker reads as resting on the surface rather than pasted flat on top.
    shadow_mask = rotated.split()[-1].point(lambda a: min(a, 90))
    shadow = Image.new("RGBA", background.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 120), (x + 4, y + 6), shadow_mask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4))

    composed = background.convert("RGBA")
    composed.alpha_composite(shadow)
    composed.alpha_composite(rotated, (x, y))

    corners = _rotated_sticker_corners(
        sticker_size=sticker.size,
        angle_degrees=angle,
        rotated_size=rotated_size,
        final_size=(st_w, st_h),
        paste_xy=(x, y),
    )
    return composed.convert("RGB"), angle, corners


def _apply_camera_artifacts(
    img: Image.Image, rng: random.Random, color_mode: Literal["color", "bw"]
) -> Image.Image:
    if color_mode == "bw":
        img = img.convert("L").convert("RGB")

    img = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.4, 1.4)))

    # Single-channel grain broadcast across RGB: real sensor grain is mostly luminance noise,
    # and keeping channels identical in bw mode is what makes it stay actually gray.
    seed = rng.randint(0, 2**31 - 1)
    grain = np.random.default_rng(seed).normal(0, rng.uniform(4, 12), (*img.size[::-1], 1))
    arr = np.clip(np.array(img).astype(np.int16) + grain, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)

    # Downsample then upsample to lose detail like a cheap, low-resolution sensor.
    w, h = img.size
    shrink = rng.uniform(0.55, 0.8)
    small = img.resize((max(1, int(w * shrink)), max(1, int(h * shrink))), Image.BILINEAR)
    return small.resize((w, h), Image.BILINEAR)


def generate_sticker_sample(
    sku: str,
    *,
    min_width: int,
    max_width: int,
    min_height: int,
    max_height: int,
    color_mode: ColorMode = "random",
    image_format: ImageFormat = "jpg",
    seed: int | None = None,
) -> tuple[bytes, StickerMetadata]:
    """Render one synthetic camera photo of a white sticker printed with `sku`,
    plus the ground-truth geometry that produced it.

    Every call (unless `seed` is fixed) picks a new canvas size, background
    surface, sticker placement/rotation, and color/bw mode, so no two stickers
    for the same SKU look identical.
    """
    sku_text = _validate_sku(sku)
    rng = random.Random(seed)

    width = rng.randint(min_width, max_width)
    height = rng.randint(min_height, max_height)
    resolved_color_mode = _resolve_color_mode(color_mode, rng)

    background = _make_background(width, height, rng)
    sticker = _draw_sticker(sku_text, rng)
    sticker_width, sticker_height = sticker.size
    composed, angle, corners = _composite(background, sticker, rng)
    final = _apply_camera_artifacts(composed, rng, resolved_color_mode)

    buf = io.BytesIO()
    if image_format == "jpg":
        final.save(buf, format="JPEG", quality=rng.randint(35, 70))
    else:
        final.save(buf, format="PNG")

    metadata = StickerMetadata(
        sku=sku_text,
        canvas_width=width,
        canvas_height=height,
        color_mode=resolved_color_mode,
        rotation_angle_degrees=angle,
        sticker_width=sticker_width,
        sticker_height=sticker_height,
        corners_xy=corners,
    )
    return buf.getvalue(), metadata


def generate_sticker_image(
    sku: str,
    *,
    min_width: int,
    max_width: int,
    min_height: int,
    max_height: int,
    color_mode: ColorMode = "random",
    image_format: ImageFormat = "jpg",
    seed: int | None = None,
) -> bytes:
    """Render one synthetic camera photo of a white sticker printed with `sku`.

    Every call (unless `seed` is fixed) picks a new canvas size, background
    surface, sticker placement/rotation, and color/bw mode, so no two stickers
    for the same SKU look identical.
    """
    image_bytes, _metadata = generate_sticker_sample(
        sku,
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        color_mode=color_mode,
        image_format=image_format,
        seed=seed,
    )
    return image_bytes
