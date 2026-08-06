"""Loads LLM/MCP-instruction prompt templates from either the MLflow Prompt
Registry or the local prompt-registry/prompts.json catalog, selected by
PROMPT_SOURCE (see settings.py). Both paths return an mlflow PromptVersion,
so callers always render variables the same way via .format(**kwargs) --
local mode isn't a separate hand-rolled templating implementation, it just
constructs a PromptVersion from the catalog entry and lets MLflow's own
{{var}} renderer do the substitution, so behavior matches mlflow mode
exactly.

PROMPT_SOURCE=mlflow raises PromptLoadError immediately if the registry or
the named prompt is unreachable/missing -- this module deliberately does not
silently fall back to the local catalog when mlflow mode was explicitly
requested, since that would defeat the point of asking for the registry.

Caching: each prompt id is loaded at most once per process (first call),
mirroring label-api's src/inference.py get_pipeline() singleton pattern.
reset_prompt_cache() is a test-only hook to force a reload, exactly like
inference.reset_pipeline_cache().
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import mlflow
from mlflow.entities.model_registry import PromptVersion

from .settings import settings

logger = logging.getLogger(__name__)

# <service>/src/prompts.py -> <service>/src -> <service> -> repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CATALOG_PATH = _REPO_ROOT / "prompt-registry" / "prompts.json"

# PromptVersion.version is typed as int; there's no registry version for
# locally-loaded prompts, so this is a placeholder -- _tag_current_trace()
# below tags the trace with the literal string "local" instead of this
# number, so it's never surfaced to a human as if it were a real version.
_LOCAL_VERSION = 0


class PromptLoadError(Exception):
    """Raised when a prompt can't be loaded from the configured PROMPT_SOURCE."""


_cache: dict[str, PromptVersion] = {}
_cache_lock = threading.Lock()
_catalog: dict[str, dict] | None = None
_catalog_lock = threading.Lock()


def _catalog_path() -> Path:
    configured = settings.PROMPT_CATALOG_PATH
    if not configured:
        return _DEFAULT_CATALOG_PATH
    path = Path(configured)
    return path if path.is_absolute() else _REPO_ROOT / path


def _load_catalog() -> dict[str, dict]:
    global _catalog
    if _catalog is not None:
        return _catalog
    with _catalog_lock:
        if _catalog is None:
            path = _catalog_path()
            try:
                data = json.loads(path.read_text())
            except OSError as exc:
                raise PromptLoadError(f"could not read local prompt catalog at {path}: {exc}") from exc
            _catalog = {entry["id"]: entry for entry in data["prompts"]}
    return _catalog


def _load(prompt_id: str) -> PromptVersion:
    if settings.PROMPT_SOURCE == "mlflow":
        import mlflow.genai  # deferred: keeps a cold import cheap when unused

        try:
            prompt = mlflow.genai.load_prompt(prompt_id)
        except Exception as exc:
            logger.exception("Failed to load prompt '%s' from the MLflow Prompt Registry", prompt_id)
            raise PromptLoadError(
                f"could not load prompt '{prompt_id}' from MLflow (PROMPT_SOURCE=mlflow): {exc}"
            ) from exc
        logger.info("Loaded prompt '%s' version %s from MLflow", prompt_id, prompt.version)
        return prompt

    if settings.PROMPT_SOURCE == "local":
        catalog = _load_catalog()
        entry = catalog.get(prompt_id)
        if entry is None:
            raise PromptLoadError(f"prompt '{prompt_id}' not found in local catalog ({_catalog_path()})")
        logger.info("Loaded prompt '%s' from local catalog %s", prompt_id, _catalog_path())
        return PromptVersion(name=prompt_id, version=_LOCAL_VERSION, template=entry["template"])

    raise PromptLoadError(f"unknown PROMPT_SOURCE {settings.PROMPT_SOURCE!r}; expected 'mlflow' or 'local'")


def get_prompt(prompt_id: str) -> PromptVersion:
    """Returns a cached PromptVersion for prompt_id (loaded once per process).
    Call .format(**kwargs) on the result to render variables. Also tags the
    current active MLflow trace/span (if any) with this prompt's name and
    version, on every call -- not just on first load -- so a cache hit in a
    later request still shows up correctly in that request's own trace."""
    prompt = _cache.get(prompt_id)
    if prompt is None:
        with _cache_lock:
            prompt = _cache.get(prompt_id)
            if prompt is None:
                prompt = _load(prompt_id)
                _cache[prompt_id] = prompt
    _tag_current_trace(prompt)
    return prompt


def _tag_current_trace(prompt: PromptVersion) -> None:
    """Best-effort only: mlflow.genai.load_prompt() does NOT auto-link a
    loaded prompt to an active trace/span (verified against the installed
    mlflow package -- it only links to an active *run* or *logged model*,
    both unrelated concepts here). Tagging the trace explicitly is
    therefore the only way to see which prompt name+version produced a
    given trace in the MLflow UI. Must never raise into the caller -- this
    is observability, not correctness."""
    try:
        if mlflow.get_current_active_span() is None:
            return
        version_label = "local" if settings.PROMPT_SOURCE == "local" else str(prompt.version)
        mlflow.update_current_trace(tags={f"prompt.{prompt.name}": version_label})
    except Exception:
        logger.debug("Could not tag current trace with prompt %s@%s", prompt.name, prompt.version, exc_info=True)


def reset_prompt_cache() -> None:
    """Test-only hook: forces the next get_prompt() call to reload instead of
    returning the cached value. Mirrors inference.reset_pipeline_cache()."""
    global _catalog
    with _cache_lock:
        _cache.clear()
    with _catalog_lock:
        _catalog = None
