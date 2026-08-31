from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8004
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


settings = Settings()
