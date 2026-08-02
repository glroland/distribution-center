from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8001
    LOCATION_NAME: str = "DC-VIRTUAL-01"
    INVENTORY_CSV_PATH: str = "data/inventory.csv"
    LOG_LEVEL: str = "INFO"

    def inventory_csv_path(self) -> Path:
        path = Path(self.INVENTORY_CSV_PATH)
        return path if path.is_absolute() else _PROJECT_ROOT / path


settings = Settings()
