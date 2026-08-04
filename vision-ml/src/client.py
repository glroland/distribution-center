"""Thin client for label-api: single-sticker fetch (for live
end-to-end eval) and bulk dataset download with ground-truth manifest (for
training data).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Sequence

import requests
from PIL import Image

from . import settings

# label-api's BulkGenerateRequest.items has max_length=200 - chunk larger
# requests so `download_dataset` can be handed arbitrarily large SKU lists.
_BULK_BATCH_LIMIT = 200


def fetch_sticker(sku: str, color_mode: str = "random", image_format: str = "jpg") -> Image.Image:
    """Fetch one live-generated sticker photo - used for the end-to-end
    pipeline's held-out eval, not for building training data (use
    `download_dataset` for that; it's a single request per image otherwise)."""
    resp = requests.get(
        f"{settings.LABEL_API_URL}/stickers/{sku}",
        params={"color_mode": color_mode, "image_format": image_format},
        timeout=30,
    )
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def _chunks(items: Sequence[tuple[str, int]], size: int) -> list[Sequence[tuple[str, int]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def download_dataset(
    items: Sequence[tuple[str, int]],
    dest_dir: Path,
    *,
    color_mode: str = "random",
    image_format: str = "jpg",
) -> Path:
    """Bulk-generate `items` ((sku, quantity) pairs) via
    `/stickers/bulk?include_manifest=true`, chunked under that endpoint's
    200-items-per-request limit, staged under `dest_dir/batch_%04d/`.

    Returns the path to a combined `manifest.jsonl` at `dest_dir` (one record
    per image, `image_path` relative to `dest_dir` so `datasets.py` can open
    images straight from it without knowing about the batch layout).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    combined_records: list[dict] = []

    for batch_idx, batch_items in enumerate(_chunks(list(items), _BULK_BATCH_LIMIT)):
        resp = requests.post(
            f"{settings.LABEL_API_URL}/stickers/bulk",
            json={
                "items": [{"sku": sku, "quantity": qty} for sku, qty in batch_items],
                "color_mode": color_mode,
                "image_format": image_format,
                "include_manifest": True,
            },
            timeout=600,
        )
        resp.raise_for_status()

        batch_dir = dest_dir / f"batch_{batch_idx:04d}"
        batch_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zip_file:
            zip_file.extractall(batch_dir)

        with open(batch_dir / "manifest.jsonl") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record["image_path"] = f"{batch_dir.name}/{record['filename']}"
                combined_records.append(record)

    combined_manifest_path = dest_dir / "manifest.jsonl"
    with open(combined_manifest_path, "w") as f:
        for record in combined_records:
            f.write(json.dumps(record) + "\n")

    return combined_manifest_path
