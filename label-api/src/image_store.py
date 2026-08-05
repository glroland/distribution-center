import logging
import time
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# How long a captured photo stays retrievable if infer_sku never gets called
# for it (e.g. the caller escalates a SKU instead of verifying it). Bounds
# memory growth from images that are captured but abandoned.
DEFAULT_TTL_SECONDS = 300.0


class ImageNotFoundError(KeyError):
    """Raised when an image_id is unknown or has expired."""


@dataclass
class _StoredImage:
    image_bytes: bytes
    media_type: str
    created_at: float


class ImageStore:
    """Short-lived, in-memory cache of captured photo bytes, keyed by an
    opaque id - lets a captured photo be referenced by MCP tool calls
    without ever putting the bytes themselves in a tool result or tool-call
    argument (see CLAUDE.md's dc-agent pipeline notes on why that matters)."""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._images: dict[str, _StoredImage] = {}

    def put(self, image_bytes: bytes, media_type: str) -> str:
        self._sweep_expired()
        image_id = uuid.uuid4().hex
        self._images[image_id] = _StoredImage(
            image_bytes=image_bytes, media_type=media_type, created_at=time.monotonic()
        )
        logger.info("Stored image %s (%d bytes, %s)", image_id, len(image_bytes), media_type)
        return image_id

    def pop(self, image_id: str) -> tuple[bytes, str]:
        stored = self._images.pop(image_id, None)
        if stored is None or (time.monotonic() - stored.created_at) > self._ttl_seconds:
            raise ImageNotFoundError(f"unknown or expired image_id: {image_id}")
        return stored.image_bytes, stored.media_type

    def _sweep_expired(self) -> None:
        now = time.monotonic()
        expired = [image_id for image_id, stored in self._images.items() if (now - stored.created_at) > self._ttl_seconds]
        for image_id in expired:
            del self._images[image_id]
        if expired:
            logger.info("Swept %d expired image(s)", len(expired))

    def reset(self) -> None:
        """Clear all stored images. Intended for test isolation."""
        self._images = {}
