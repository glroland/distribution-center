"""Bulk sticker generation: write a batch of images to a local folder, zip it, return the zip path."""

from __future__ import annotations

import re
import shutil
import uuid
import zipfile
from pathlib import Path

from .stickers import ColorMode, ImageFormat, generate_sticker_image

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_stem(sku: str) -> str:
    return _UNSAFE_FILENAME_CHARS.sub("-", sku.strip().upper()).strip("-") or "SKU"


def generate_bulk_zip(
    items: list[tuple[str, int]],
    *,
    output_dir: Path,
    min_width: int,
    max_width: int,
    min_height: int,
    max_height: int,
    color_mode: ColorMode = "random",
    image_format: ImageFormat = "jpg",
    cleanup_after_zip: bool = False,
) -> Path:
    """Generate one sticker image per requested (sku, quantity) pair and zip them.

    Images are written to `output_dir/batch-<uuid>/` before zipping, so the
    raw files stay inspectable on disk (unless `cleanup_after_zip` is set),
    same as any other locally-staged demo output in this repo.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_dir = output_dir / f"batch-{uuid.uuid4().hex}"
    batch_dir.mkdir(parents=True)

    extension = "jpg" if image_format == "jpg" else "png"
    counters: dict[str, int] = {}
    for sku, quantity in items:
        for _ in range(quantity):
            counters[sku] = counters.get(sku, 0) + 1
            image_bytes = generate_sticker_image(
                sku,
                min_width=min_width,
                max_width=max_width,
                min_height=min_height,
                max_height=max_height,
                color_mode=color_mode,
                image_format=image_format,
            )
            filename = f"{_safe_filename_stem(sku)}_{counters[sku]:03d}.{extension}"
            (batch_dir / filename).write_bytes(image_bytes)

    zip_path = output_dir / f"{batch_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in sorted(batch_dir.iterdir()):
            zip_file.write(file_path, arcname=file_path.name)

    if cleanup_after_zip:
        shutil.rmtree(batch_dir)

    return zip_path
