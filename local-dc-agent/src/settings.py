from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    PO_INGEST_API_URL: str = "http://localhost:8000"
    WMS_API_URL: str = "http://localhost:8001"
    ROBOT_API_URL: str = "http://localhost:8002"
    SUPERVISOR_API_URL: str = "http://localhost:8003"
    SHIPPING_API_URL: str = "http://localhost:8004"
    LABEL_API_URL: str = "http://localhost:8005"
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-5"
    OPENAI_BASE_URL: str | None = None
    MAX_FULFILLMENT_TURNS: int = 20
    HOST: str = "0.0.0.0"
    PORT: int = 9100
    AGENT_URL: str | None = None
    LOG_LEVEL: str = "INFO"
    MLFLOW_TRACKING_URI: str | None = None

    @model_validator(mode="after")
    def _default_agent_url(self) -> "Settings":
        if self.AGENT_URL is None:
            self.AGENT_URL = f"http://localhost:{self.PORT}/"
        return self


settings = Settings()
