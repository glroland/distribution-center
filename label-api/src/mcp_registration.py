"""Registers this service's MCP server (and keeps its discovered tool list
fresh) in MLflow's MCP Server Registry -- mlflow.genai.register_mcp_server /
refresh_mcp_server_version_tools, experimental as of mlflow==3.15.0.

Also records this version's remotes[] as approved *access endpoints*
(mlflow.genai.create_mcp_access_endpoint) rather than leaving MLflow to show
the raw server.json remote URL as an unmanaged "official" endpoint -- access
endpoints are what MLflow's own UI/consumers are meant to connect through.
There is no server.json field for this (access endpoints are a registry-side
concept, not part of the MCP server.json schema), so it has to happen here.

Reuses the exact same MLFLOW_TRACKING_URI / MLFLOW_WORKSPACE /
MLFLOW_TRACKING_AUTH env vars tracing.py and prompts.py already rely on --
mlflow reads all three natively from the environment (see tracing.py's
configure_tracing() docstring), so nothing is set explicitly here. Disabled
outright, like tracing, when no tracking URI is configured; independently
toggleable via MCP_AUTODISCOVERY_ENABLED for a deployment that has tracing
on but doesn't want registry writes.

The server.json payload is read from this service's own server.json (sibling
of src/, see settings.MCP_SERVER_JSON_PATH) rather than duplicated as
hardcoded name/version strings, so the registry entry can never drift from
what's actually published there.

Registration scrapes this service's own live /mcp endpoint back through
MLflow to discover tools, so it can only succeed once this process is
actually accepting connections. configure_mcp_registration() is meant to be
awaited from app.py's lifespan *after* the MCP app's own lifespan has
started (not at import time, before uvicorn has bound its socket) -- it
fires the actual registration call as a background task, after a short
configurable delay, so it never blocks startup or the request that MLflow
itself sends back into this same process.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from mlflow.exceptions import MlflowException

from .settings import settings

logger = logging.getLogger(__name__)

# <service>/src/mcp_registration.py -> <service>/src -> <service>
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SERVER_JSON_PATH = _SERVICE_ROOT / "server.json"

# Keeps the background task alive -- asyncio only holds a weak reference to
# a task once its creator drops the local variable, and configure_mcp_registration()
# returns immediately after scheduling it.
_background_tasks: set[asyncio.Task] = set()


def _server_json_path() -> Path:
    configured = settings.MCP_SERVER_JSON_PATH
    if not configured:
        return _DEFAULT_SERVER_JSON_PATH
    path = Path(configured)
    return path if path.is_absolute() else _SERVICE_ROOT / path


def _source_url(server_json: dict) -> str | None:
    repository = server_json.get("repository") or {}
    if not repository.get("url"):
        return None
    subfolder = repository.get("subfolder", "")
    return f"{repository['url']}/blob/main/{subfolder}/server.json"


def _ensure_access_endpoints(name: str, version: str, server_json: dict) -> None:
    """Backfills access endpoints for a server version that was registered
    before this existed, or by a call that hit the RESOURCE_ALREADY_EXISTS
    branch below (register_mcp_server's create_access_endpoints_from_remotes
    only fires on the call that actually creates the version). Idempotent --
    safe to call on every startup."""
    import mlflow.genai

    remotes = server_json.get("remotes") or []
    if not remotes:
        return
    try:
        existing_urls = {
            ep.url
            for ep in mlflow.genai.search_mcp_access_endpoints(server_name=name, server_version=version)
        }
    except Exception:
        logger.warning("Could not list existing MCP access endpoints for %s@%s", name, version, exc_info=True)
        return

    for remote in remotes:
        url = remote.get("url")
        if not url or url in existing_urls:
            continue
        try:
            mlflow.genai.create_mcp_access_endpoint(
                server_name=name,
                url=url,
                transport_type=remote.get("type", "streamable-http"),
                server_version=version,
            )
            logger.info("Created MCP access endpoint for %s@%s -> %s", name, version, url)
        except MlflowException as exc:
            if exc.error_code == "RESOURCE_ALREADY_EXISTS":
                logger.debug("MCP access endpoint for %s@%s -> %s already exists", name, version, url)
            else:
                logger.warning(
                    "Could not create MCP access endpoint for %s@%s -> %s: %s", name, version, url, exc
                )
        except Exception:
            logger.warning(
                "Could not create MCP access endpoint for %s@%s -> %s", name, version, url, exc_info=True
            )


def _register_and_refresh() -> None:
    import mlflow.genai  # deferred: keeps a cold import cheap when unused, same as prompts.py

    server_json = json.loads(_server_json_path().read_text())
    name = server_json["name"]
    version = server_json["version"]

    try:
        mlflow.genai.register_mcp_server(
            server_json=server_json,
            source=_source_url(server_json),
            status="active",
            create_access_endpoints_from_remotes=True,
        )
        logger.info("Registered MCP server %s@%s in MLflow", name, version)
    except MlflowException as exc:
        if exc.error_code == "RESOURCE_ALREADY_EXISTS":
            logger.debug("MCP server %s@%s already registered in MLflow", name, version)
        else:
            logger.warning("Could not register MCP server %s@%s in MLflow: %s", name, version, exc)
    except Exception:
        logger.warning("Could not register MCP server %s@%s in MLflow", name, version, exc_info=True)

    _ensure_access_endpoints(name, version, server_json)

    try:
        mlflow.genai.refresh_mcp_server_version_tools(name=name, version=version)
        logger.info("Refreshed MCP tool discovery for %s@%s", name, version)
    except Exception:
        logger.warning("Could not refresh MCP tool discovery for %s@%s", name, version, exc_info=True)


async def configure_mcp_registration() -> None:
    """Schedules background MCP autodiscovery registration/refresh. Never
    raises and never blocks the caller -- registry connectivity problems
    must not prevent this service from serving traffic."""
    if not settings.MLFLOW_TRACKING_URI or not settings.MCP_AUTODISCOVERY_ENABLED:
        return

    async def _run() -> None:
        await asyncio.sleep(settings.MCP_AUTODISCOVERY_STARTUP_DELAY_SECONDS)
        try:
            await asyncio.to_thread(_register_and_refresh)
        except Exception:
            logger.warning("MCP tool autodiscovery failed", exc_info=True)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
