import functools
import inspect
import logging
import os

import mlflow
from mcp import MCPError
from mcp.server.mcpserver.exceptions import ToolError

from .settings import settings

logger = logging.getLogger(__name__)

SERVICE_NAME = "local-wms-api"


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
    """Wrap an MCP tool function in an MLflow span named `<service>.<tool>`,
    and surface whatever it raises to the calling model.

    The MCP SDK (2.x) only forwards a `ToolError`'s message to the caller;
    any other exception is reported as a bare "Error executing tool <name>"
    with the detail kept in this server's log. Tools here raise plain domain
    exceptions (unknown SKU, insufficient quantity, ...) whose message is
    exactly what the calling agent needs to decide what to do next, so they're
    re-raised as `ToolError` -- outside the span, so the trace still records
    the original exception type."""
    traced = mlflow.trace(fn, span_type="TOOL", name=f"{SERVICE_NAME}.{fn.__name__}")

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await traced(*args, **kwargs)
            except (ToolError, MCPError):
                raise
            except Exception as exc:
                raise ToolError(str(exc)) from exc

    else:

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return traced(*args, **kwargs)
            except (ToolError, MCPError):
                raise
            except Exception as exc:
                raise ToolError(str(exc)) from exc

    return wrapper
