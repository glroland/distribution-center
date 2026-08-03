"""MLflow experiment tracking for training runs - no-ops if MLFLOW_TRACKING_URI
is unset, same as every other service's src/tracing.py in this repo, so
notebooks work fully offline too. MLFLOW_TRACKING_URI/MLFLOW_WORKSPACE/
MLFLOW_TRACKING_TOKEN are read natively by mlflow straight from the process
environment (populated by settings.py's load_dotenv) - nothing to wire up
here beyond deciding whether tracking should run and picking the experiment.
"""

from __future__ import annotations

import mlflow

from . import settings


def is_enabled() -> bool:
    return settings.MLFLOW_TRACKING_URI is not None


def configure_tracking(stage_name: str) -> None:
    if not is_enabled():
        return
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    mlflow.set_experiment(f"vision-ml/{stage_name}")
