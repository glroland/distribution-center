import base64
import io
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP as MCPServer
from PIL import Image

from .inference import get_pipeline
from .settings import settings

logger = logging.getLogger(__name__)


def build_mcp_server() -> MCPServer:
    """Build an MCP server exposing this service's local SKU inference to an
    LLM tool-calling loop, so verifying a picked item's sticker is an
    explicit tool call rather than orchestration hidden in application code."""

    mcp_server = MCPServer(
        name="label-api",
        instructions=(
            "SKU inference. infer_sku takes a base64-encoded photo of a sticker - "
            "e.g. the image_base64 a picking robot's get_item_photo tool returns - "
            "and reads the SKU printed on it, alongside a 0-1 confidence score. Use "
            "this to visually verify that what was actually picked matches the SKU "
            "you intended to fetch: if the returned sku doesn't match what you "
            "expected, or confidence is low, treat that as a real signal that the "
            "shelf sticker doesn't read as the SKU you asked for (a mispick or a "
            "mislabeled shelf) rather than shipping it regardless. Inference runs "
            "entirely against checkpoints bundled into this service's own process - "
            "it never calls out to another service to do the actual reading."
        ),
        host=settings.HOST,
        streamable_http_path="/",
    )

    @mcp_server.tool()
    def infer_sku(image_base64: str) -> dict:
        """Predict the SKU printed on a sticker photo. `image_base64` is the raw
        image bytes (jpg or png), base64-encoded. Returns {sku, confidence
        (0.0-1.0), bbox, angle_degrees, inference_ms}. An empty sku or a
        confidence well below 1.0 means the photo didn't read cleanly - treat
        that as a reason to double-check (e.g. escalate to a human), not
        something to silently ignore."""
        try:
            image_bytes = base64.b64decode(image_base64, validate=True)
        except Exception as exc:
            logger.warning("infer_sku tool call rejected: image_base64 is not valid base64 (%s)", exc)
            raise ValueError(f"image_base64 is not valid base64: {exc}") from None

        try:
            image = Image.open(io.BytesIO(image_bytes))
            image.load()
        except Exception as exc:
            logger.warning("infer_sku tool call rejected: not a readable image (%s)", exc)
            raise ValueError(f"invalid image: {exc}") from None

        pipeline = get_pipeline(
            models_dir=Path(settings.INFERENCE_MODELS_DIR),
            device=settings.INFERENCE_DEVICE,
            pad_frac=settings.INFERENCE_PAD_FRAC,
        )
        prediction = pipeline.predict(image)
        return {
            "sku": prediction.sku,
            "confidence": prediction.confidence,
            "bbox": list(prediction.bbox),
            "angle_degrees": prediction.angle_degrees,
            "inference_ms": prediction.inference_ms,
        }

    return mcp_server
