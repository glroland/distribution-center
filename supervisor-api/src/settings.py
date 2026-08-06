from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8003
    LOG_LEVEL: str = "INFO"

    # Odds that a requested SKU turns out to be unavailable at every other
    # distribution center when an agent asks for an inventory transfer.
    TRANSFER_UNAVAILABLE_CHANCE: float = 1 / 3
    MLFLOW_TRACKING_URI: str | None = None
    PROMPT_SOURCE: str = "local"
    PROMPT_CATALOG_PATH: str | None = None


settings = Settings()
