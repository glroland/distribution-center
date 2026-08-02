import logging
import os

import mlflow

from .settings import settings

logger = logging.getLogger(__name__)


def configure_tracing() -> None:
    """Enable MLflow tracing for this agent's LLM calls and outbound MCP tool calls.

    MLFLOW_TRACKING_URI, MLFLOW_WORKSPACE, MLFLOW_TRACKING_AUTH, and friends are
    read natively by mlflow from the environment -- nothing to configure here
    beyond deciding whether tracing should run at all. Without a tracking URI
    (e.g. in tests or local dev without MLflow running), tracing is disabled
    outright so nothing is written to a local ./mlruns directory.

    mlflow.openai.autolog() is safe to call unconditionally: it patches the
    OpenAI client to emit a span per call, but those spans are no-ops while
    tracing is disabled.
    """
    mlflow.openai.autolog()
    if not settings.MLFLOW_TRACKING_URI:
        mlflow.tracing.disable()
        return
    os.environ.setdefault("MLFLOW_EXPERIMENT_NAME", "distribution-center")
    logger.info("MLflow tracing enabled: uri=%s", settings.MLFLOW_TRACKING_URI)
