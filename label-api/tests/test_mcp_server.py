import asyncio
import base64
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src import inference
from src.app import mcp_server
from src.settings import settings
from src.stickers import generate_sticker_image


@pytest.fixture(autouse=True)
def _reset_pipeline_cache():
    inference.reset_pipeline_cache()
    yield
    inference.reset_pipeline_cache()


def _call(tool: str, args: dict) -> dict:
    result = asyncio.run(mcp_server.call_tool(tool, args))
    text = "".join(part.text for part in result if hasattr(part, "text"))
    return json.loads(text)


def _sticker_base64(sku: str, seed: int | None = None) -> str:
    image_bytes = generate_sticker_image(
        sku,
        min_width=settings.MIN_IMAGE_WIDTH,
        max_width=settings.MAX_IMAGE_WIDTH,
        min_height=settings.MIN_IMAGE_HEIGHT,
        max_height=settings.MAX_IMAGE_HEIGHT,
        color_mode="random",
        image_format="jpg",
        seed=seed,
    )
    return base64.b64encode(image_bytes).decode("ascii")


def test_infer_sku_tool_returns_sku_and_confidence() -> None:
    body = _call("infer_sku", {"image_base64": _sticker_base64("SKU-1002", seed=2)})

    assert body["sku"] == "SKU-1002"
    assert 0.0 <= body["confidence"] <= 1.0
    assert len(body["bbox"]) == 4
    assert isinstance(body["angle_degrees"], float)
    assert body["inference_ms"] > 0


def test_infer_sku_tool_rejects_invalid_base64() -> None:
    with pytest.raises(ToolError):
        _call("infer_sku", {"image_base64": "not-base64!!!"})


def test_infer_sku_tool_rejects_non_image_bytes() -> None:
    not_an_image = base64.b64encode(b"hello world").decode("ascii")
    with pytest.raises(ToolError):
        _call("infer_sku", {"image_base64": not_an_image})
