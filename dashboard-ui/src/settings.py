import json
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import DistributionCenter

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8090
    LOG_LEVEL: str = "INFO"
    # Base URL the *other* services use to reach this dashboard, e.g. for the
    # dc-agent's progress webhook callbacks. Override if not running on localhost.
    PUBLIC_URL: str = "http://localhost:8090"

    SUPERVISOR_API_URL: str = "http://localhost:8003"
    PO_INGEST_API_URL: str = "http://localhost:8000"
    LABEL_GENERATOR_API_URL: str = "http://localhost:8005"

    # Sample PO PDFs baked into this service's own image (see data/pos/ and its
    # COPY in the Containerfile) -- always searched first, in every environment,
    # so the dashboard has something to pick from even with no PO_DIRS override.
    PACKAGED_PO_DIR: Path = _PROJECT_ROOT / "data" / "pos"

    # Comma-separated additional directories (relative paths resolve against this
    # project's parent directory) to search for demo PO PDFs, e.g. those produced by
    # `make generate-pos` or already checked into test-po-generator/output. These are
    # local-dev conveniences only -- unlike PACKAGED_PO_DIR they aren't copied into
    # the container image.
    PO_DIRS: str = "target/pos,test-po-generator/output"

    def po_dirs(self) -> list[Path]:
        repo_root = _PROJECT_ROOT.parent
        dirs = [self.PACKAGED_PO_DIR]
        for raw in self.PO_DIRS.split(","):
            raw = raw.strip()
            if not raw:
                continue
            path = Path(raw)
            dirs.append(path if path.is_absolute() else repo_root / path)
        return dirs


settings = Settings()

# One entry per distribution center, mirroring deploy/helm/values.yaml's
# `distributionCenters` list. Add another entry here (with its own agent/wms/robot/
# shipping URLs) to make a second DC selectable in the UI.
_DEFAULT_DISTRIBUTION_CENTERS = [
    DistributionCenter(
        name="distribution-center-a",
        display_name="Distribution Center A",
        agent_url="http://localhost:9100",
        wms_url="http://localhost:8001",
        robot_url="http://localhost:8002",
        shipping_url="http://localhost:8004",
        grid_width=10,
        grid_height=10,
        dock_x=0,
        dock_y=0,
    )
]

# In Kubernetes the DCs live at in-cluster Service DNS names rather than
# localhost, so the Helm chart (deploy/helm/charts/dashboardUi) renders this
# list as JSON instead of hand-editing this file per deployment.
_distribution_centers_json = os.environ.get("DISTRIBUTION_CENTERS_JSON")
DISTRIBUTION_CENTERS: list[DistributionCenter] = (
    [DistributionCenter(**entry) for entry in json.loads(_distribution_centers_json)]
    if _distribution_centers_json
    else _DEFAULT_DISTRIBUTION_CENTERS
)

DC_BY_NAME: dict[str, DistributionCenter] = {dc.name: dc for dc in DISTRIBUTION_CENTERS}
