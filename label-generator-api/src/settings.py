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


settings = Settings()
