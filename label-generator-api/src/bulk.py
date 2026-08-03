"""Bulk sticker generation: write a batch of images to a local folder, zip it, return the zip path."""

from __future__ import annotations

import json
import re
import shutil
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path

from .stickers import ColorMode, ImageFormat, generate_sticker_sample

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

MANIFEST_FILENAME = "manifest.jsonl"


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
    include_manifest: bool = False,
) -> Path:
    """Generate one sticker image per requested (sku, quantity) pair and zip them.

    Images are written to `output_dir/batch-<uuid>/` before zipping, so the
    raw files stay inspectable on disk (unless `cleanup_after_zip` is set),
    same as any other locally-staged demo output in this repo.

    If `include_manifest` is set, a `manifest.jsonl` file is written
    alongside the images (and included in the zip) with one JSON object per
    image: `filename`, `sku`, `canvas_width`, `canvas_height`, `color_mode`,
    `rotation_angle_degrees`, `sticker_width`, `sticker_height`, and
    `corners_xy` (the 4 corners of the sticker rectangle in that image's
    pixel coordinates) - ground truth for training models against this
    generator's output, not needed just to look at the pictures.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_dir = output_dir / f"batch-{uuid.uuid4().hex}"
    batch_dir.mkdir(parents=True)

    extension = "jpg" if image_format == "jpg" else "png"
    counters: dict[str, int] = {}
    manifest_lines: list[str] = []
    for sku, quantity in items:
        for _ in range(quantity):
            counters[sku] = counters.get(sku, 0) + 1
            image_bytes, metadata = generate_sticker_sample(
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
            if include_manifest:
                record = {"filename": filename, **asdict(metadata)}
                manifest_lines.append(json.dumps(record))

    if include_manifest:
        (batch_dir / MANIFEST_FILENAME).write_text("\n".join(manifest_lines) + "\n")

    zip_path = output_dir / f"{batch_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in sorted(batch_dir.iterdir()):
            zip_file.write(file_path, arcname=file_path.name)

    if cleanup_after_zip:
        shutil.rmtree(batch_dir)

    return zip_path
