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
    LOG_LEVEL: str = "INFO"

    def shelves_csv_path(self) -> Path:
        path = Path(self.SHELVES_CSV_PATH)
        return path if path.is_absolute() else _PROJECT_ROOT / path


settings = Settings()
