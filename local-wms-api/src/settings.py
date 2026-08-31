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
    MLFLOW_TRACKING_URI: str | None = None
    PROMPT_SOURCE: str = "local"
    PROMPT_CATALOG_PATH: str | None = None

    # MCP tool autodiscovery (src/mcp_registration.py): registers this
    # service in MLflow's MCP Server Registry and refreshes its discovered
    # tool list on every startup. Only takes effect when MLFLOW_TRACKING_URI
    # is set; this is an independent off-switch for deployments that trace
    # but don't want registry writes.
    MCP_AUTODISCOVERY_ENABLED: bool = True
    MCP_AUTODISCOVERY_STARTUP_DELAY_SECONDS: float = 2.0
    MCP_SERVER_JSON_PATH: str | None = None

    def inventory_csv_path(self) -> Path:
        path = Path(self.INVENTORY_CSV_PATH)
        return path if path.is_absolute() else _PROJECT_ROOT / path


settings = Settings()
