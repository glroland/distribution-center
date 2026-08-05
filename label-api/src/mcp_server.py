import io
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP as MCPServer
from PIL import Image

from .image_store import ImageNotFoundError, ImageStore
from .inference import get_pipeline
from .settings import settings

logger = logging.getLogger(__name__)


def build_mcp_server(image_store: ImageStore) -> MCPServer:
    """Build an MCP server exposing this service's local SKU inference to an
    LLM tool-calling loop, so verifying a picked item's sticker is an
    explicit tool call rather than orchestration hidden in application code."""

    mcp_server = MCPServer(
        name="label-api",
        instructions=(
            "SKU inference. infer_sku takes the image_id of a previously captured "
            "sticker photo - e.g. the image_id a picking robot's get_item_photo "
            "tool returns - and reads the SKU printed on it, alongside a 0-1 "
            "confidence score. Use this to visually verify that what was actually "
            "picked matches the SKU you intended to fetch: if the returned sku "
            "doesn't match what you expected, or confidence is low, treat that as "
            "a real signal that the shelf sticker doesn't read as the SKU you "
            "asked for (a mispick or a mislabeled shelf) rather than shipping it "
            "regardless. Inference runs entirely against checkpoints bundled into "
            "this service's own process - it never calls out to another service "
            "to do the actual reading. The photo itself is fetched locally from "
            "this service's own image store, keyed by image_id - never pass "
            "image bytes directly, only the id."
        ),
        host=settings.HOST,
        streamable_http_path="/",
    )

    @mcp_server.tool()
    def infer_sku(image_id: str) -> dict:
        """Predict the SKU printed on a previously captured sticker photo.
        `image_id` is the id returned by a photo-capture tool (e.g. a picking
        robot's get_item_photo). Returns {sku, confidence (0.0-1.0), bbox,
        angle_degrees, inference_ms}. An empty sku or a confidence well below
        1.0 means the photo didn't read cleanly - treat that as a reason to
        double-check (e.g. escalate to a human), not something to silently
        ignore. Each image_id can only be read once - re-calling with the
        same id after a successful read will fail."""
        try:
            image_bytes, _media_type = image_store.pop(image_id)
        except ImageNotFoundError as exc:
            logger.warning("infer_sku tool call rejected: %s", exc)
            raise ValueError(str(exc)) from None

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
