from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8002
    SHELVES_CSV_PATH: str = "data/shelves.csv"
    GRID_WIDTH: int = 10
    GRID_HEIGHT: int = 10
    DOCK_X: int = 0
    DOCK_Y: int = 0
    CARRY_CAPACITY: int = 100
    MOVE_STEP_DELAY_SECONDS: float = 0.25
    LOG_LEVEL: str = "INFO"
    MLFLOW_TRACKING_URI: str | None = None

    # Where get_item_photo (src/mcp_server.py) fetches a picked SKU's shelf
    # sticker photo from, to hand to label-api's own infer_sku tool for
    # visual pick verification.
    LABEL_API_URL: str = "http://localhost:8005"

    def shelves_csv_path(self) -> Path:
        path = Path(self.SHELVES_CSV_PATH)
        return path if path.is_absolute() else _PROJECT_ROOT / path


settings = Settings()
