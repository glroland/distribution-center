import logging
import os

import mlflow

from .settings import settings

logger = logging.getLogger(__name__)

SERVICE_NAME = "supervisor-api"


def configure_tracing() -> None:
    """Enable MLflow tracing for this service's MCP tool calls.

    MLFLOW_TRACKING_URI, MLFLOW_WORKSPACE, MLFLOW_TRACKING_AUTH, and friends are
    read natively by mlflow from the environment -- nothing to configure here
    beyond deciding whether tracing should run at all. Without a tracking URI
    (e.g. in tests or local dev without MLflow running), tracing is disabled
    outright so nothing is written to a local ./mlruns directory.
    """
    if not settings.MLFLOW_TRACKING_URI:
        mlflow.tracing.disable()
        return
    os.environ.setdefault("MLFLOW_EXPERIMENT_NAME", "distribution-center")
    logger.info("MLflow tracing enabled: uri=%s", settings.MLFLOW_TRACKING_URI)


def tool_trace(fn):
    """Wrap an MCP tool function in an MLflow span named `<service>.<tool>`."""
    return mlflow.trace(fn, span_type="TOOL", name=f"{SERVICE_NAME}.{fn.__name__}")
