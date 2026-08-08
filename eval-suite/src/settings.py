from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Same downstream services local-dc-agent talks to, called directly here
    # for white-box benchmarks (extraction) and inspected live for schema
    # validation (mcp-trajectory), plus the agent itself for black-box runs.
    AGENT_URL: str = "http://localhost:9100/"
    PO_INGEST_API_URL: str = "http://localhost:8000"
    WMS_API_URL: str = "http://localhost:8001"
    ROBOT_API_URL: str = "http://localhost:8002"
    SUPERVISOR_API_URL: str = "http://localhost:8003"
    SHIPPING_API_URL: str = "http://localhost:8004"
    LABEL_API_URL: str = "http://localhost:8005"

    # Used only by the extraction benchmark, which calls the model directly
    # (mirrors local-dc-agent's own OPENAI_* / PROMPT_SOURCE contract so the
    # exact prompt version dc-agent would use is what gets evaluated).
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-5"
    OPENAI_BASE_URL: str | None = None
    OPENAI_REQUEST_TIMEOUT_SECONDS: float = 60.0
    MLFLOW_TRACKING_URI: str | None = None
    PROMPT_SOURCE: str = "local"
    PROMPT_CATALOG_PATH: str | None = None

    # Webhook receiver used by the mcp-trajectory and end-to-end benchmarks to
    # capture local-dc-agent's per-tool-call progress events (see
    # local-dc-agent/src/agent_executor.py:_build_progress_hook).
    WEBHOOK_HOST: str = "127.0.0.1"
    WEBHOOK_PORT: int = 0  # 0 = let the OS pick a free port

    AGENT_CALL_TIMEOUT_SECONDS: float = 300.0

    # seed_data.py/prompts.py default to locating products.csv,
    # local-wms-api/data/inventory.csv, etc. by walking up from this file's
    # own location (correct for a full repo checkout, e.g. local CLI use).
    # The Containerfile flattens that layout (see its own comment), so it
    # sets this instead of relying on path-walking.
    REPO_ROOT_OVERRIDE: str | None = None

    # Pass thresholds, mirrored in config/evalhub.yaml's pass_criteria -- keep
    # both in sync by hand (same pattern dashboard-ui/settings.py already
    # uses for its DISTRIBUTION_CENTER mirror, per CLAUDE.md).
    EXTRACTION_FIELD_ACCURACY_THRESHOLD: float = 0.90
    MCP_TRAJECTORY_SCORE_THRESHOLD: float = 0.90
    END_TO_END_SUCCESS_THRESHOLD: float = 0.80


settings = Settings()
