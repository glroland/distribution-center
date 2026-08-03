"""MLflow experiment tracking for training runs - no-ops if MLFLOW_TRACKING_URI
is unset, same as every other service's src/tracing.py in this repo, so
notebooks work fully offline too. MLFLOW_TRACKING_URI/MLFLOW_WORKSPACE/
MLFLOW_TRACKING_TOKEN are read natively by mlflow straight from the process
environment (populated by settings.py's load_dotenv) - nothing to wire up
here beyond deciding whether tracking should run and picking the experiment.
"""

from __future__ import annotations

import os

import mlflow

from . import settings


def is_enabled() -> bool:
    return settings.MLFLOW_TRACKING_URI is not None


def configure_tracking(stage_name: str) -> None:
    if not is_enabled():
        return
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    mlflow.set_experiment(f"vision-ml/{stage_name}")


def configure_mlflow_env(tracking_uri: str, workspace: str, token: str = "", tracking_auth: str = "") -> None:
    """Populate the same env vars `configure_tracking()` relies on `load_dotenv`
    for, but from explicit values instead - used by `pipeline.py`'s KFP
    components, which run in a container with no `.env` and get these as
    pipeline parameters instead. `token` and `tracking_auth` are mutually
    exclusive (mirrors the Helm chart's per-service choice between a token
    and the in-cluster `kubernetes-namespaced` auth provider): prefer an
    explicit token if one is given, otherwise fall back to the auth provider.
    """
    os.environ["MLFLOW_TRACKING_URI"] = tracking_uri
    os.environ["MLFLOW_WORKSPACE"] = workspace
    if token:
        os.environ["MLFLOW_TRACKING_TOKEN"] = token
        os.environ.pop("MLFLOW_TRACKING_AUTH", None)
    elif tracking_auth:
        os.environ["MLFLOW_TRACKING_AUTH"] = tracking_auth
        os.environ.pop("MLFLOW_TRACKING_TOKEN", None)
