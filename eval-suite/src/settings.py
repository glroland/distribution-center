from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Set by the EvalHub operator itself in every k8s Job pod (never present
    # for local dev) -- used below to pick in-cluster Service DNS defaults
    # over localhost ones. Provider ConfigMaps have their own runtime.k8s.env
    # field (see config/evalhub-provider.yaml) that looks like the place for
    # this, but it's a dead end: confirmed empirically that values set there
    # are accepted and echoed back by `evalhub providers list`, yet never
    # actually reach the Job pod's own environment -- a gap in this EvalHub
    # build, not something fixable from provider config. Without a working
    # default here, dc-mcp-trajectory's first line (fetch_live_tool_schemas())
    # tried to open an MCP session against http://localhost:8001/mcp and
    # crashed uncaught (see mcp_schema_client.py's own docstring for why
    # that call has no timeout/retry wrapping it) -- the k8s Job's
    # backoffLimit retried a few times and gave up, and EvalHub itself never
    # learned the run failed (no final status callback from a pod that never
    # got that far), leaving the dashboard showing "running" indefinitely.
    EVALHUB_MODE: str = "local"

    # Same downstream services local-dc-agent talks to, called directly here
    # for white-box benchmarks (extraction) and inspected live for schema
    # validation (mcp-trajectory), plus the agent itself for black-box runs.
    # Left as None so _default_service_urls() below can tell "unset" apart
    # from "explicitly overridden" and only fill in a mode-appropriate
    # default for the former.
    AGENT_URL: str | None = None
    PO_INGEST_API_URL: str | None = None
    WMS_API_URL: str | None = None
    ROBOT_API_URL: str | None = None
    SUPERVISOR_API_URL: str | None = None
    SHIPPING_API_URL: str | None = None
    LABEL_API_URL: str | None = None

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

    @model_validator(mode="after")
    def _default_service_urls(self) -> "Settings":
        namespace = "distribution-center"
        # (field name, port) -- k8s default is that Helm release's Service
        # DNS (adc-<component>.<namespace>.svc.cluster.local:<port>,
        # matching the release name "adc" every other adc-* Service in this
        # namespace already uses); local default matches each service's own
        # Makefile-driven `make start-all` port.
        services = [
            ("AGENT_URL", "dc-agent", 9100, "/"),
            ("PO_INGEST_API_URL", "po-ingest-api", 8000, ""),
            ("WMS_API_URL", "wms-api", 8001, ""),
            ("ROBOT_API_URL", "robot-api", 8002, ""),
            ("SUPERVISOR_API_URL", "supervisor-api", 8003, ""),
            ("SHIPPING_API_URL", "shipping-api", 8004, ""),
            ("LABEL_API_URL", "label-api", 8005, ""),
        ]
        for field, component, port, suffix in services:
            if getattr(self, field) is not None:
                continue
            if self.EVALHUB_MODE == "k8s":
                value = f"http://adc-{component}.{namespace}.svc.cluster.local:{port}{suffix}"
            else:
                value = f"http://localhost:{port}{suffix}"
            setattr(self, field, value)
        return self


settings = Settings()
