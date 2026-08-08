"""Entrypoint EvalHub invokes for BYOF jobs against any of eval-suite's three
benchmarks, dispatching on JobSpec.benchmark_id -- mirrors the single-script,
benchmark_id-dispatch pattern used by eval-hub-sdk's own reference BYOF
adapter (github.com/williamcaban/edd-demo's providers/hr_rag_adapter.py).

Run by EvalHub as: `python -m src.evalhub_entrypoint`, with
EVALHUB_JOB_SPEC_PATH pointing at the job spec file it writes -- see
config/evalhub.yaml's provider.runtime.k8s.entrypoint.

Requires eval-hub-sdk: pip install eval-hub-sdk
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from evalhub.adapter.callbacks import DefaultCallbacks

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
    job_spec_path = os.environ.get("EVALHUB_JOB_SPEC_PATH")
    if not job_spec_path:
        raise RuntimeError(
            "EVALHUB_JOB_SPEC_PATH is not set. This entrypoint is meant to be invoked "
            "by EvalHub as a BYOF adapter. For local runs without EvalHub, use "
            "`python -m src` instead (see README.md)."
        )

    spec = json.loads(Path(job_spec_path).read_text())
    benchmark_id = spec.get("benchmark_id")
    adapter_cls = _ADAPTERS_BY_BENCHMARK.get(benchmark_id)
    if adapter_cls is None:
        raise ValueError(
            f"Unknown benchmark_id {benchmark_id!r}. Expected one of: {list(_ADAPTERS_BY_BENCHMARK)}"
        )

    logger.info("eval-suite BYOF entrypoint starting - benchmark: %s", benchmark_id)
    adapter = adapter_cls(job_spec_path=job_spec_path)
    callbacks = DefaultCallbacks.from_adapter(adapter)
    try:
        results = adapter.run_benchmark_job(adapter.job_spec, callbacks)
        callbacks.report_results(results)
        logger.info(
            "EVALUATION COMPLETE  benchmark=%s  score=%.3f", results.benchmark_id, results.overall_score
        )
    except Exception:
        logger.exception("Evaluation failed")
        raise


if __name__ == "__main__":
    main()
