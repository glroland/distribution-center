import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src import inference
from src.app import image_store, mcp_server
from src.settings import settings
from src.stickers import generate_sticker_image


@pytest.fixture(autouse=True)
def _reset_pipeline_cache():
    inference.reset_pipeline_cache()
    yield
    inference.reset_pipeline_cache()


@pytest.fixture(autouse=True)
def _reset_image_store():
    image_store.reset()
    yield
    image_store.reset()


def _call(tool: str, args: dict) -> dict:
    result = asyncio.run(mcp_server.call_tool(tool, args))
    text = "".join(part.text for part in result if hasattr(part, "text"))
    return json.loads(text)


def _store_sticker(sku: str, seed: int | None = None) -> str:
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
    return image_store.put(image_bytes, "image/jpeg")


def test_infer_sku_tool_returns_sku_and_confidence() -> None:
    body = _call("infer_sku", {"image_id": _store_sticker("SKU-1002", seed=2)})

    assert body["sku"] == "SKU-1002"
    assert 0.0 <= body["confidence"] <= 1.0
    assert len(body["bbox"]) == 4
    assert isinstance(body["angle_degrees"], float)
    assert body["inference_ms"] > 0


def test_infer_sku_tool_rejects_unknown_image_id() -> None:
    with pytest.raises(ToolError):
        _call("infer_sku", {"image_id": "not-a-real-id"})


def test_infer_sku_tool_rejects_reused_image_id() -> None:
    image_id = _store_sticker("SKU-1003", seed=3)
    _call("infer_sku", {"image_id": image_id})

    with pytest.raises(ToolError):
        _call("infer_sku", {"image_id": image_id})


def test_infer_sku_tool_rejects_non_image_bytes() -> None:
    image_id = image_store.put(b"hello world", "image/jpeg")
    with pytest.raises(ToolError):
        _call("infer_sku", {"image_id": image_id})
