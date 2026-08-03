"""PyTorch Dataset classes for the 3 training stages. All three read the same
manifest.jsonl (written by `client.download_dataset`) and derive their own
(input, target) pair from it via `geometry.py` - one generated dataset serves
all three stages, no per-stage data collection needed.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset

from . import geometry, models


def load_manifest(manifest_path: Path) -> list[dict]:
    records = []
    with open(manifest_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            record["corners_xy"] = [tuple(point) for point in record["corners_xy"]]
            records.append(record)
    return records


def split_records(
    records: Sequence[dict],
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 0,
    held_out_skus: Sequence[str] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """SKU-level split (no split shares a SKU string with another) so the OCR
    stage is actually evaluated on generalization, not memorization.
    `held_out_skus` (e.g. the real product catalog) are always routed to the
    test split, on top of whatever test_frac worth of the remaining
    vocabulary is also held out."""
    held_out = set(held_out_skus or [])
    remaining_skus = sorted({r["sku"] for r in records} - held_out)
    rng = random.Random(seed)
    rng.shuffle(remaining_skus)

    n_val = int(len(remaining_skus) * val_frac)
    n_test = int(len(remaining_skus) * test_frac)
    val_skus = set(remaining_skus[:n_val])
    test_skus = set(remaining_skus[n_val : n_val + n_test]) | held_out
    train_skus = set(remaining_skus[n_val + n_test :])

    def _bucket(bucket_skus: set[str]) -> list[dict]:
        return [r for r in records if r["sku"] in bucket_skus]

    return _bucket(train_skus), _bucket(val_skus), _bucket(test_skus)


def image_to_tensor(image: Image.Image) -> torch.Tensor:
    return torch.from_numpy(np.array(image)).permute(2, 0, 1).float() / 255.0


class LocalizationDataset(Dataset):
    """Full raw photo -> normalized sticker bbox target."""

    def __init__(self, records: Sequence[dict], images_root: Path, input_size: int = models.LocalizerNet.INPUT_SIZE):
        self.records = list(records)
        self.images_root = Path(images_root)
        self.input_size = input_size

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        record = self.records[idx]
        image = Image.open(self.images_root / record["image_path"]).convert("RGB")
        canvas_size = (record["canvas_width"], record["canvas_height"])

        bbox = geometry.corners_to_bbox(record["corners_xy"])
        target = geometry.normalize_bbox(bbox, canvas_size)

        image = image.resize((self.input_size, self.input_size), Image.BILINEAR)
        return image_to_tensor(image), torch.tensor(target, dtype=torch.float32)


class OrientationDataset(Dataset):
    """Padded crop from the *ground-truth* bbox, jittered to resemble a real
    (imperfect) stage-1 prediction - see `geometry.jitter_bbox` - -> (sin,
    cos) rotation target. The target angle is unaffected by the jitter
    (rotation doesn't depend on where exactly the crop's edges land)."""

    def __init__(
        self,
        records: Sequence[dict],
        images_root: Path,
        pad_frac: float = 0.2,
        input_size: int = models.OrientationNet.INPUT_SIZE,
        jitter: bool = True,
    ):
        self.records = list(records)
        self.images_root = Path(images_root)
        self.pad_frac = pad_frac
        self.input_size = input_size
        self.jitter = jitter

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        record = self.records[idx]
        image = Image.open(self.images_root / record["image_path"]).convert("RGB")
        canvas_size = (record["canvas_width"], record["canvas_height"])
        corners = record["corners_xy"]

        bbox = geometry.corners_to_bbox(corners)
        if self.jitter:
            bbox = geometry.jitter_bbox(bbox, canvas_size)
        padded = geometry.pad_bbox(bbox, self.pad_frac, canvas_size)
        crop = image.crop(padded).resize((self.input_size, self.input_size), Image.BILINEAR)

        angle = geometry.corners_to_angle_degrees(corners)
        sin_v, cos_v = geometry.angle_to_sin_cos(angle)
        return image_to_tensor(crop), torch.tensor([sin_v, cos_v], dtype=torch.float32)


class OcrDataset(Dataset):
    """Ground-truth padded crop, de-rotated upright -> SKU character sequence
    (as class indices) for `nn.CTCLoss`.

    Both the crop's bbox and the de-rotation angle are jittered around their
    ground-truth values (see `geometry.jitter_bbox` and `angle_jitter_std`
    below) to resemble what stage 1's and stage 2's actual (imperfect)
    predictions produce at inference time - training only against
    pixel-perfect ground truth here left this stage fragile to that real
    imprecision (`04_end_to_end_pipeline.ipynb`'s chained accuracy was far
    below what each stage's isolated accuracy suggested it should be, until
    this was added)."""

    def __init__(
        self,
        records: Sequence[dict],
        images_root: Path,
        pad_frac: float = 0.2,
        height: int = models.CRNNReader.HEIGHT,
        width: int = models.CRNNReader.WIDTH,
        jitter: bool = True,
        angle_jitter_std: float = 8.0,
    ):
        self.records = list(records)
        self.images_root = Path(images_root)
        self.pad_frac = pad_frac
        self.height = height
        self.width = width
        self.jitter = jitter
        self.angle_jitter_std = angle_jitter_std

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, str]:
        record = self.records[idx]
        image = Image.open(self.images_root / record["image_path"]).convert("RGB")
        canvas_size = (record["canvas_width"], record["canvas_height"])
        corners = record["corners_xy"]

        bbox = geometry.corners_to_bbox(corners)
        if self.jitter:
            bbox = geometry.jitter_bbox(bbox, canvas_size)
        padded = geometry.pad_bbox(bbox, self.pad_frac, canvas_size)
        crop = image.crop(padded)

        angle = geometry.corners_to_angle_degrees(corners)
        if self.jitter:
            angle += random.gauss(0, self.angle_jitter_std)
        upright = geometry.rotate_upright(crop, angle).resize((self.width, self.height), Image.BILINEAR)

        sku = record["sku"]
        target = torch.tensor([models.CHAR_TO_IDX[char] for char in sku], dtype=torch.long)
        return image_to_tensor(upright), target, sku


def ocr_collate(
    batch: Sequence[tuple[torch.Tensor, torch.Tensor, str]],
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, list[str]]:
    """`nn.CTCLoss` wants targets concatenated (not padded) plus their individual lengths."""
    images, targets, skus = zip(*batch)
    target_lengths = torch.tensor([len(t) for t in targets], dtype=torch.long)
    return torch.stack(images), torch.cat(targets), target_lengths, list(skus)
