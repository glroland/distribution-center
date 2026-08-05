import io
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image

from .bulk import generate_bulk_zip
from .image_store import ImageStore
from .inference import get_pipeline
from .mcp_server import build_mcp_server
from .models import BulkGenerateRequest, CapturedImage, SkuInferenceResult
from .settings import settings
from .stickers import ColorMode, ImageFormat, InvalidSkuError, generate_sticker_image

logger = logging.getLogger(__name__)

image_store = ImageStore(ttl_seconds=settings.IMAGE_TTL_SECONDS)
mcp_server = build_mcp_server(image_store)
mcp_app = mcp_server.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
        yield


app = FastAPI(title="Label API", lifespan=lifespan)
app.mount("/mcp", mcp_app)

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


@app.post("/stickers/{sku}/capture")
def capture_sticker(
    sku: str,
    color_mode: ColorMode = Query("random"),
    image_format: ImageFormat = Query("jpg"),
) -> CapturedImage:
    """Generate one sticker photo for `sku`, same as `GET /stickers/{sku}`,
    but store it server-side and return a small JSON reference instead of
    streaming the bytes back - lets a caller (e.g. an MCP tool) hand off an
    image_id instead of raw image bytes."""
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
    media_type = _MEDIA_TYPES[image_format]
    image_id = image_store.put(image_bytes, media_type)
    return CapturedImage(image_id=image_id, sku=sku, media_type=media_type)


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
        include_manifest=body.include_manifest,
    )
    return FileResponse(zip_path, media_type="application/zip", filename=zip_path.name)


@app.post("/infer")
async def infer_sku(image: UploadFile = File(...)) -> SkuInferenceResult:
    contents = await image.read()
    logger.info(
        "infer request received: filename=%r content_type=%r size_bytes=%d",
        image.filename, image.content_type, len(contents),
    )

    try:
        pil_image = Image.open(io.BytesIO(contents))
        pil_image.load()
    except Exception as exc:
        logger.warning("infer request rejected: %r is not a readable image (%s)", image.filename, exc)
        raise HTTPException(status_code=400, detail=f"invalid image: {exc}") from None

    try:
        pipeline = get_pipeline(
            models_dir=Path(settings.INFERENCE_MODELS_DIR),
            device=settings.INFERENCE_DEVICE,
            pad_frac=settings.INFERENCE_PAD_FRAC,
        )
    except Exception as exc:
        logger.error("infer request failed: SKU inference models unavailable (%s)", exc)
        raise HTTPException(status_code=503, detail=f"SKU inference models unavailable: {exc}") from None

    prediction = pipeline.predict(pil_image)
    return SkuInferenceResult(
        sku=prediction.sku,
        confidence=prediction.confidence,
        bbox=prediction.bbox,
        angle_degrees=prediction.angle_degrees,
        inference_ms=prediction.inference_ms,
    )
