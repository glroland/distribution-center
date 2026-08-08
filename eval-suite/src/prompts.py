"""Loads dc-agent's prompt templates from the same source dc-agent itself
would use (the MLflow Prompt Registry, or local-dc-agent's own local
prompts.json catalog), so the extraction benchmark evaluates the actual
prompt production runs -- not a copy that can silently drift from it.

This is a deliberately minimal mirror of local-dc-agent/src/prompts.py (no
per-process caching, no trace tagging -- eval-suite runs are one-shot CLI
invocations, not a long-lived request-serving process), not a re-import of
it: services in this repo are independent packages (see CLAUDE.md's
"Per-service shape"), so this hand-mirrors the load path the same way
dashboard-ui/settings.py hand-mirrors DISTRIBUTION_CENTER config.
"""

from __future__ import annotations

import json
from pathlib import Path

from mlflow.entities.model_registry import PromptVersion

from .settings import settings

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _repo_root() -> Path:
    return Path(settings.REPO_ROOT_OVERRIDE) if settings.REPO_ROOT_OVERRIDE else _DEFAULT_REPO_ROOT


_DEFAULT_CATALOG_PATH = _repo_root() / "local-dc-agent" / "prompts.json"


class PromptLoadError(Exception):
    """Raised when a prompt can't be loaded from the configured PROMPT_SOURCE."""


def _catalog_path() -> Path:
    configured = settings.PROMPT_CATALOG_PATH
    if not configured:
        return _DEFAULT_CATALOG_PATH
    path = Path(configured)
    return path if path.is_absolute() else _repo_root() / path


def load_prompt(prompt_id: str) -> PromptVersion:
    """Returns an mlflow PromptVersion; call .format() to render it. Both
    PROMPT_SOURCE branches return the same type so callers never need to care
    which one is active, exactly like local-dc-agent/src/prompts.py."""
    if settings.PROMPT_SOURCE == "mlflow":
        import mlflow.genai  # deferred: keeps a cold import cheap when unused

        try:
            return mlflow.genai.load_prompt(prompt_id)
        except Exception as exc:
            raise PromptLoadError(
                f"could not load prompt '{prompt_id}' from MLflow (PROMPT_SOURCE=mlflow): {exc}"
            ) from exc

    if settings.PROMPT_SOURCE == "local":
        try:
            data = json.loads(_catalog_path().read_text())
        except OSError as exc:
            raise PromptLoadError(f"could not read local prompt catalog at {_catalog_path()}: {exc}") from exc
        entries = {entry["id"]: entry for entry in data["prompts"]}
        entry = entries.get(prompt_id)
        if entry is None:
            raise PromptLoadError(f"prompt '{prompt_id}' not found in local catalog ({_catalog_path()})")
        return PromptVersion(name=prompt_id, version=0, template=entry["template"])

    raise PromptLoadError(f"unknown PROMPT_SOURCE {settings.PROMPT_SOURCE!r}; expected 'mlflow' or 'local'")
