"""Entrypoint EvalHub invokes for BYOF jobs against any of eval-suite's three
benchmarks, dispatching on JobSpec.benchmark_id -- mirrors the single-script,
benchmark_id-dispatch pattern used by eval-hub-sdk's own reference BYOF
adapter (github.com/williamcaban/edd-demo's providers/hr_rag_adapter.py).

Run by EvalHub as: `python -m src.evalhub_entrypoint`. The job spec is
*mounted*, not passed via EVALHUB_JOB_SPEC_PATH -- the operator never sets
that env var; a real k8s Job pod's `adapter` container only gets
EVALHUB_MODE=k8s, MLFLOW_*. `evalhub.adapter.config.get_job_spec_path()`
already knows this (EVALHUB_MODE=k8s -> /meta/job.json, matching the actual
ConfigMap mount -- see FrameworkAdapter.__init__'s own docstring), so this
must be used instead of hand-rolling the same lookup (which requiring
EVALHUB_JOB_SPEC_PATH got wrong -- confirmed by a real k8s run crashing
with "EVALHUB_JOB_SPEC_PATH is not set").

Requires eval-hub-sdk: pip install eval-hub-sdk[adapter]
"""

from __future__ import annotations

import json
import logging

from evalhub.adapter.callbacks import DefaultCallbacks
from evalhub.adapter.config import get_job_spec_path

from .adapters.end_to_end_adapter import BENCHMARK_ID as END_TO_END_ID
from .adapters.end_to_end_adapter import EndToEndFrameworkAdapter
from .adapters.extraction_adapter import BENCHMARK_ID as EXTRACTION_ID
from .adapters.extraction_adapter import ExtractionFrameworkAdapter
from .adapters.mcp_trajectory_adapter import BENCHMARK_ID as MCP_TRAJECTORY_ID
from .adapters.mcp_trajectory_adapter import McpTrajectoryFrameworkAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_ADAPTERS_BY_BENCHMARK = {
    EXTRACTION_ID: ExtractionFrameworkAdapter,
    MCP_TRAJECTORY_ID: McpTrajectoryFrameworkAdapter,
    END_TO_END_ID: EndToEndFrameworkAdapter,
}


def main() -> None:
    job_spec_path = get_job_spec_path()
    spec = json.loads(job_spec_path.read_text())
    benchmark_id = spec.get("benchmark_id")
    adapter_cls = _ADAPTERS_BY_BENCHMARK.get(benchmark_id)
    if adapter_cls is None:
        raise ValueError(
            f"Unknown benchmark_id {benchmark_id!r}. Expected one of: {list(_ADAPTERS_BY_BENCHMARK)}"
        )

    logger.info("eval-suite BYOF entrypoint starting - benchmark: %s", benchmark_id)
    adapter = adapter_cls(job_spec_path=str(job_spec_path))
    callbacks = DefaultCallbacks.from_adapter(adapter)
    try:
        results = adapter.run_benchmark_job(adapter.job_spec, callbacks)
        run_id = callbacks.mlflow.save(results, adapter.job_spec)
        if run_id:
            results.mlflow_run_id = run_id
        callbacks.report_results(results)
        logger.info(
            "EVALUATION COMPLETE  benchmark=%s  score=%.3f", results.benchmark_id, results.overall_score
        )
    except Exception:
        logger.exception("Evaluation failed")
        raise


if __name__ == "__main__":
    main()
