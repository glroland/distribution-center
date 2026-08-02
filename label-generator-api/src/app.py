import io
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from .bulk import generate_bulk_zip
from .models import BulkGenerateRequest
from .settings import settings
from .stickers import ColorMode, ImageFormat, InvalidSkuError, generate_sticker_image

app = FastAPI(title="Label Generator API")

_MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/stickers/{sku}")
def generate_sticker(
    sku: str,
    color_mode: ColorMode = Query("random"),
    image_format: ImageFormat = Query("jpg"),
) -> StreamingResponse:
    try:
        image_bytes = generate_sticker_image(
            sku,
            min_width=settings.MIN_IMAGE_WIDTH,
            max_width=settings.MAX_IMAGE_WIDTH,
            min_height=settings.MIN_IMAGE_HEIGHT,
            max_height=settings.MAX_IMAGE_HEIGHT,
            color_mode=color_mode,
            image_format=image_format,
        )
    except InvalidSkuError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return StreamingResponse(io.BytesIO(image_bytes), media_type=_MEDIA_TYPES[image_format])


@app.post("/stickers/bulk")
def generate_stickers_bulk(body: BulkGenerateRequest) -> FileResponse:
    zip_path = generate_bulk_zip(
        [(item.sku, item.quantity) for item in body.items],
        output_dir=Path(settings.BULK_OUTPUT_DIR),
        min_width=settings.MIN_IMAGE_WIDTH,
        max_width=settings.MAX_IMAGE_WIDTH,
        min_height=settings.MIN_IMAGE_HEIGHT,
        max_height=settings.MAX_IMAGE_HEIGHT,
        color_mode=body.color_mode,
        image_format=body.image_format,
        cleanup_after_zip=settings.BULK_CLEANUP_AFTER_ZIP,
    )
    return FileResponse(zip_path, media_type="application/zip", filename=zip_path.name)
