"""Chains the 3 trained models to read a SKU off one raw sticker photo:
localize -> pad+crop -> orient -> derotate -> read. Used by
`04_end_to_end_pipeline.ipynb` for the true, compounding-error end-to-end
evaluation (each stage's own notebook trains and evaluates it in isolation
against ground truth instead)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import torch
from PIL import Image

from . import geometry, models
from .datasets import image_to_tensor


@dataclass
class SkuPrediction:
    sku: str
    bbox: geometry.BBox
    angle_degrees: float
    crop: Image.Image  # the upright, cropped image actually fed to the OCR model - handy for
    # visualizing where the pipeline went wrong (blank/garbled crop -> a localization or
    # orientation error, not an OCR error).


class SkuExtractionPipeline:
    def __init__(
        self,
        localizer_path: Path,
        orientation_path: Path,
        ocr_path: Path,
        device: str = "cpu",
        pad_frac: float = 0.2,
    ):
        self.device = torch.device(device)
        self.pad_frac = pad_frac

        self.localizer = models.LocalizerNet().to(self.device)
        self.localizer.load_state_dict(torch.load(localizer_path, map_location=self.device))
        self.localizer.eval()

        self.orientation = models.OrientationNet().to(self.device)
        self.orientation.load_state_dict(torch.load(orientation_path, map_location=self.device))
        self.orientation.eval()

        self.ocr = models.CRNNReader().to(self.device)
        self.ocr.load_state_dict(torch.load(ocr_path, map_location=self.device))
        self.ocr.eval()

    @torch.no_grad()
    def predict(self, image: Image.Image) -> SkuPrediction:
        image = image.convert("RGB")
        canvas_size = image.size

        loc_input = image.resize((models.LocalizerNet.INPUT_SIZE,) * 2, Image.BILINEAR)
        norm_bbox = self.localizer(image_to_tensor(loc_input).unsqueeze(0).to(self.device))[0].cpu().tolist()
        bbox = geometry.denormalize_bbox(norm_bbox, canvas_size)
        padded_bbox = geometry.pad_bbox(bbox, self.pad_frac, canvas_size)
        crop = image.crop(padded_bbox)

        orient_input = crop.resize((models.OrientationNet.INPUT_SIZE,) * 2, Image.BILINEAR)
        sin_v, cos_v = self.orientation(image_to_tensor(orient_input).unsqueeze(0).to(self.device))[0].cpu().tolist()
        angle = geometry.sin_cos_to_angle(sin_v, cos_v)

        upright = geometry.rotate_upright(crop, angle)
        ocr_input = upright.resize((models.CRNNReader.WIDTH, models.CRNNReader.HEIGHT), Image.BILINEAR)
        log_probs = self.ocr(image_to_tensor(ocr_input).unsqueeze(0).to(self.device))
        sku = models.greedy_ctc_decode(log_probs)[0]

        return SkuPrediction(sku=sku, bbox=bbox, angle_degrees=angle, crop=upright)
