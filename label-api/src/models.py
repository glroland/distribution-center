from typing import Literal

from pydantic import BaseModel, Field, field_validator

ColorModeField = Literal["color", "bw", "random"]
ImageFormatField = Literal["jpg", "png"]


class BulkGenerateItem(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    quantity: int = Field(default=1, ge=1, le=500)

    @field_validator("sku")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sku must not be blank")
        return value


class BulkGenerateRequest(BaseModel):
    items: list[BulkGenerateItem] = Field(min_length=1, max_length=200)
    color_mode: ColorModeField = "random"
    image_format: ImageFormatField = "jpg"
    include_manifest: bool = False


class SkuInferenceResult(BaseModel):
    sku: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: tuple[float, float, float, float]
    angle_degrees: float
    inference_ms: float
