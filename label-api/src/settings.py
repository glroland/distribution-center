from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8005
    LOG_LEVEL: str = "INFO"

    # Every generated image picks its own random size in these ranges - there is no fixed output size.
    MIN_IMAGE_WIDTH: int = 480
    MAX_IMAGE_WIDTH: int = 900
    MIN_IMAGE_HEIGHT: int = 360
    MAX_IMAGE_HEIGHT: int = 700

    # Local folder bulk-generated images/zips are staged in before being returned.
    BULK_OUTPUT_DIR: str = "output"
    # If true, the per-batch image folder is deleted after zipping, leaving only the zip on disk.
    BULK_CLEANUP_AFTER_ZIP: bool = False

    # SKU inference (see src/inference.py) - vision-ml-trained checkpoints,
    # bundled into this service's own Docker image at build time (see
    # Containerfile). Loaded from disk and run in-process, not called out to
    # a separate inference service.
    INFERENCE_MODELS_DIR: str = "models"
    INFERENCE_DEVICE: str = "cpu"
    INFERENCE_PAD_FRAC: float = 0.2

    # How long a photo captured via POST /stickers/{sku}/capture stays
    # retrievable by infer_sku if never consumed (see src/image_store.py).
    IMAGE_TTL_SECONDS: float = 300.0


settings = Settings()
