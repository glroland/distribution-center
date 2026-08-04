"""Local SKU inference: loads the 3 PyTorch checkpoints the `vision-ml`
project trains (localize -> orient -> OCR) from `models/` - bundled into
this service's own Docker image at build time, see the Containerfile - and
chains them to read a SKU off a sticker photo.

This is a self-contained copy of vision-ml's inference-time code
(`src/models.py`'s nn.Module defs, the inference-relevant slice of
`src/geometry.py`, and `src/inference.py`'s `SkuExtractionPipeline`), not an
import of that project: label-api and vision-ml are independent services,
each with their own venv/requirements/Containerfile (see the root
CLAUDE.md), and a deployed label-api image can't assume a vision-ml
checkout is available next to it. If vision-ml's model architectures,
OCR_CHARSET, or geometry conventions ever change, this file has to be
updated by hand to match - otherwise checkpoints won't load, or will load
but predict nonsense.

Deliberately not calling out to a separate inference/model-serving process:
`get_pipeline()` loads all 3 checkpoints once per label-api process and
`SkuExtractionPipeline.predict()` runs them in-process on CPU, so a
prediction never depends on any other service being reachable.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch import nn

logger = logging.getLogger(__name__)

# Must match the charset vision-ml's ocr.pt checkpoint was trained against
# (src/models.py's OCR_CHARSET there) - label-api's own sticker generator
# always upper-cases SKUs, which is why this doesn't need lowercase letters.
OCR_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
BLANK_IDX = 0
CHAR_TO_IDX = {char: idx + 1 for idx, char in enumerate(OCR_CHARSET)}
IDX_TO_CHAR = {idx: char for char, idx in CHAR_TO_IDX.items()}
NUM_CLASSES = len(OCR_CHARSET) + 1  # + blank

BBox = tuple[float, float, float, float]  # xmin, ymin, xmax, ymax


# ---------------------------------------------------------------------------
# Model architectures - must exactly match vision-ml/src/models.py, since
# these load state dicts trained by that project.
# ---------------------------------------------------------------------------


def _conv_block(in_channels: int, out_channels: int, pool: tuple[int, int] | int | None = 2) -> nn.Sequential:
    layers = [
        nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1),
        nn.BatchNorm2d(out_channels),
        nn.ReLU(inplace=True),
    ]
    if pool is not None:
        layers.append(nn.MaxPool2d(pool))
    return nn.Sequential(*layers)


class LocalizerNet(nn.Module):
    """Full raw photo (resized to INPUT_SIZE x INPUT_SIZE) -> normalized
    (xmin, ymin, xmax, ymax) bbox of the sticker."""

    INPUT_SIZE = 224

    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(
            _conv_block(3, 16),
            _conv_block(16, 32),
            _conv_block(32, 64),
            _conv_block(64, 128),
            _conv_block(128, 128),
        )
        self.head = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(128, 64),
            nn.ReLU(inplace=True),
            nn.Linear(64, 4),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.features(x))


class OrientationNet(nn.Module):
    """Padded sticker crop (resized to INPUT_SIZE x INPUT_SIZE) -> unit
    (sin, cos) of the rotation angle."""

    INPUT_SIZE = 128

    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(
            _conv_block(3, 16),
            _conv_block(16, 32),
            _conv_block(32, 64),
            _conv_block(64, 128),
        )
        self.head = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(128, 32),
            nn.ReLU(inplace=True),
            nn.Linear(32, 2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.head(self.features(x))
        return out / out.norm(dim=1, keepdim=True).clamp_min(1e-6)


class CRNNReader(nn.Module):
    """Upright sticker crop (resized to HEIGHT x WIDTH) -> per-timestep
    log-probabilities over `OCR_CHARSET` + blank, for CTC decoding."""

    HEIGHT = 32
    WIDTH = 160

    def __init__(self, num_classes: int = NUM_CLASSES, rnn_hidden: int = 128) -> None:
        super().__init__()
        self.cnn = nn.Sequential(
            _conv_block(3, 32, pool=2),
            _conv_block(32, 64, pool=2),
            _conv_block(64, 128, pool=(2, 1)),
            _conv_block(128, 128, pool=None),
        )
        collapsed_height = self.HEIGHT // 8
        self.rnn = nn.LSTM(128 * collapsed_height, rnn_hidden, num_layers=2, bidirectional=True, batch_first=True)
        self.classifier = nn.Linear(rnn_hidden * 2, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        features = self.cnn(x)  # (B, C, H', W')
        b, c, h, w = features.shape
        features = features.permute(0, 3, 1, 2).reshape(b, w, c * h)
        rnn_out, _ = self.rnn(features)
        logits = self.classifier(rnn_out)  # (B, W', num_classes)
        log_probs = logits.log_softmax(dim=2)
        return log_probs.permute(1, 0, 2)  # (T, B, num_classes)


def image_to_tensor(image: Image.Image) -> torch.Tensor:
    return torch.from_numpy(np.array(image)).permute(2, 0, 1).float() / 255.0


# ---------------------------------------------------------------------------
# Geometry - inference-time subset of vision-ml/src/geometry.py (training-only
# helpers like jitter_bbox aren't needed here).
# ---------------------------------------------------------------------------


def denormalize_bbox(norm_bbox: list[float], canvas_size: tuple[int, int]) -> BBox:
    cw, ch = canvas_size
    x0, y0, x1, y1 = norm_bbox
    return (x0 * cw, y0 * ch, x1 * cw, y1 * ch)


def pad_bbox(bbox: BBox, pad_frac: float, canvas_size: tuple[int, int]) -> tuple[int, int, int, int]:
    xmin, ymin, xmax, ymax = bbox
    pad = pad_frac * max(xmax - xmin, ymax - ymin)
    cw, ch = canvas_size
    return (
        max(0, int(xmin - pad)),
        max(0, int(ymin - pad)),
        min(cw, int(xmax + pad)),
        min(ch, int(ymax + pad)),
    )


def sin_cos_to_angle(sin_v: float, cos_v: float) -> float:
    return math.degrees(math.atan2(sin_v, cos_v)) % 360


def rotate_upright(image: Image.Image, angle_degrees: float) -> Image.Image:
    """See vision-ml/src/geometry.py's `rotate_upright` docstring for the
    derivation of why this angle convention is correct, not off by a sign."""
    return image.rotate(angle_degrees, resample=Image.BICUBIC, expand=False)


def greedy_ctc_decode_with_confidence(log_probs: torch.Tensor) -> list[tuple[str, float]]:
    """Best-path greedy CTC decode, same collapse rule as vision-ml's
    `greedy_ctc_decode` (drop blanks, collapse repeats), plus a confidence
    score per sequence: the mean softmax probability of the model's own
    chosen class at each timestep that actually contributed a character to
    the decoded string (blank / repeated-frame timesteps don't count - they
    don't correspond to an emitted character, and including them would
    understate confidence on longer blank-heavy sequences).

    `log_probs` is (T, B, num_classes). Returns one (sku, confidence) pair
    per batch item; confidence is 0.0 for an empty decode.
    """
    probs = log_probs.exp()  # (T, B, C) - log_probs came from log_softmax, so this is a real distribution
    best_probs, best_idx = probs.max(dim=2)  # each (T, B)
    best_idx = best_idx.transpose(0, 1).tolist()  # (B, T)
    best_probs = best_probs.transpose(0, 1).tolist()  # (B, T)

    results: list[tuple[str, float]] = []
    for sequence, seq_probs in zip(best_idx, best_probs):
        chars = []
        kept_probs = []
        previous = None
        for idx, prob in zip(sequence, seq_probs):
            if idx != BLANK_IDX and idx != previous:
                chars.append(IDX_TO_CHAR[idx])
                kept_probs.append(prob)
            previous = idx
        sku = "".join(chars)
        confidence = sum(kept_probs) / len(kept_probs) if kept_probs else 0.0
        results.append((sku, confidence))
    return results


@dataclass
class SkuPrediction:
    sku: str
    confidence: float
    bbox: BBox
    angle_degrees: float
    inference_ms: float


class SkuExtractionPipeline:
    """Loads the localizer/orientation/ocr checkpoints once (construction is
    the expensive part - tens to hundreds of ms just for `torch.load` x3)
    and reuses them for every `predict()` call. Construct via `get_pipeline()`
    below rather than directly, so a process only ever loads one instance."""

    def __init__(self, models_dir: Path, device: str = "cpu", pad_frac: float = 0.2):
        self.device = torch.device(device)
        self.pad_frac = pad_frac

        localizer_path = models_dir / "localizer.pt"
        orientation_path = models_dir / "orientation.pt"
        ocr_path = models_dir / "ocr.pt"
        for path in (localizer_path, orientation_path, ocr_path):
            if not path.is_file():
                raise FileNotFoundError(
                    f"missing SKU inference checkpoint: {path} - expected models/localizer.pt, "
                    "orientation.pt, and ocr.pt to be present (bundled into the Docker image at "
                    "build time, or copied in locally for `python -m src`)"
                )

        logger.info("loading SKU extraction models from %s (device=%s, pad_frac=%s)", models_dir, device, pad_frac)
        load_start = time.monotonic()

        self.localizer = LocalizerNet().to(self.device)
        self.localizer.load_state_dict(torch.load(localizer_path, map_location=self.device))
        self.localizer.eval()
        logger.debug("loaded localizer checkpoint from %s", localizer_path)

        self.orientation = OrientationNet().to(self.device)
        self.orientation.load_state_dict(torch.load(orientation_path, map_location=self.device))
        self.orientation.eval()
        logger.debug("loaded orientation checkpoint from %s", orientation_path)

        self.ocr = CRNNReader().to(self.device)
        self.ocr.load_state_dict(torch.load(ocr_path, map_location=self.device))
        self.ocr.eval()
        logger.debug("loaded ocr checkpoint from %s", ocr_path)

        logger.info("SKU extraction pipeline ready in %.1fms", (time.monotonic() - load_start) * 1000)

    @torch.no_grad()
    def predict(self, image: Image.Image) -> SkuPrediction:
        start = time.monotonic()
        image = image.convert("RGB")
        canvas_size = image.size
        logger.debug("running SKU inference on a %dx%d image", *canvas_size)

        loc_input = image.resize((LocalizerNet.INPUT_SIZE,) * 2, Image.BILINEAR)
        norm_bbox = self.localizer(image_to_tensor(loc_input).unsqueeze(0).to(self.device))[0].cpu().tolist()
        bbox = denormalize_bbox(norm_bbox, canvas_size)
        padded_bbox = pad_bbox(bbox, self.pad_frac, canvas_size)
        logger.debug("localizer: bbox=%s padded_bbox=%s", tuple(round(v, 1) for v in bbox), padded_bbox)
        crop = image.crop(padded_bbox)

        orient_input = crop.resize((OrientationNet.INPUT_SIZE,) * 2, Image.BILINEAR)
        sin_v, cos_v = self.orientation(image_to_tensor(orient_input).unsqueeze(0).to(self.device))[0].cpu().tolist()
        angle = sin_cos_to_angle(sin_v, cos_v)
        logger.debug("orientation: angle=%.1fdeg", angle)

        upright = rotate_upright(crop, angle)
        ocr_input = upright.resize((CRNNReader.WIDTH, CRNNReader.HEIGHT), Image.BILINEAR)
        log_probs = self.ocr(image_to_tensor(ocr_input).unsqueeze(0).to(self.device))
        sku, confidence = greedy_ctc_decode_with_confidence(log_probs)[0]

        inference_ms = (time.monotonic() - start) * 1000
        logger.info(
            "sku inference complete: sku=%r confidence=%.3f angle=%.1fdeg bbox=%s inference_ms=%.1f",
            sku, confidence, angle, tuple(round(v, 1) for v in bbox), inference_ms,
        )
        return SkuPrediction(sku=sku, confidence=confidence, bbox=bbox, angle_degrees=angle, inference_ms=inference_ms)


# ---------------------------------------------------------------------------
# Process-wide singleton - loading 3 checkpoints per request would be slow
# and pointless, since none of them are mutated after load.
# ---------------------------------------------------------------------------

_pipeline: SkuExtractionPipeline | None = None
_pipeline_lock = threading.Lock()


def get_pipeline(models_dir: Path, device: str = "cpu", pad_frac: float = 0.2) -> SkuExtractionPipeline:
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                try:
                    _pipeline = SkuExtractionPipeline(models_dir=models_dir, device=device, pad_frac=pad_frac)
                except Exception:
                    logger.exception("failed to load SKU extraction models from %s", models_dir)
                    raise
    return _pipeline


def reset_pipeline_cache() -> None:
    """Test-only hook: forces the next `get_pipeline()` call to reload from
    disk instead of returning the cached singleton."""
    global _pipeline
    with _pipeline_lock:
        _pipeline = None
