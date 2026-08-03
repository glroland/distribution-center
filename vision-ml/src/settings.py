"""Plain env-var settings, loaded from a `.env` found by walking up from the
current working directory (works whether a notebook's cwd is `vision-ml/` or
`vision-ml/notebooks/`). Deliberately not pydantic-settings: `configure_tracking()`
in `tracking.py` needs MLFLOW_TRACKING_URI/MLFLOW_WORKSPACE/MLFLOW_TRACKING_TOKEN
to land in the real process environment (mlflow reads those natively, same as
every other service's src/tracing.py in this repo), and `load_dotenv` is what
actually does that - a validated-but-separate settings object wouldn't.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LABEL_GENERATOR_API_URL = os.environ.get("LABEL_GENERATOR_API_URL", "http://localhost:8005")

_data_dir = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR = _data_dir if _data_dir.is_absolute() else PROJECT_ROOT / _data_dir
RAW_DIR = DATA_DIR / "raw"
MODELS_DIR = DATA_DIR / "models"

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI") or None
