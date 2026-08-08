"""Bridges a benchmark's plain `run_local()` result into the real EvalHub
BYOF contract (`evalhub.adapter.FrameworkAdapter`, from the `eval-hub-sdk`
package: https://github.com/eval-hub/eval-hub-sdk) when that SDK is
installed and this benchmark is being run by EvalHub itself. Importing
`evalhub` is deferred into make_framework_adapter() so `python -m src run`
(the local, no-cluster-required path) never needs `eval-hub-sdk` installed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable


@dataclass
class LocalRunResult:
    """Benchmark output in a form both `python -m src run`'s CLI report and
    the EvalHub JobResults conversion below can consume."""

    benchmark_id: str
    score: float
    threshold: float
    metrics: dict[str, float]
    num_examples: int
    details: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold


class EvalHubNotInstalled(RuntimeError):
    pass


def make_framework_adapter(benchmark_id: str, run_local_fn: Callable[[], LocalRunResult]):
    """Returns a FrameworkAdapter subclass whose run_benchmark_job() calls
    run_local_fn() (benchmark parameters come from settings.py / env vars --
    the same way local-dc-agent itself is configured -- rather than from
    EvalHub's JobSpec.parameters, so a benchmark behaves identically whether
    invoked locally or by EvalHub) and converts the result into JobResults."""
    try:
        from evalhub.adapter import (
            EvaluationResult,
            FrameworkAdapter,
            JobCallbacks,
            JobPhase,
            JobResults,
            JobSpec,
            JobStatus,
            JobStatusUpdate,
            MessageInfo,
        )
    except ImportError as exc:
        raise EvalHubNotInstalled(
            "eval-hub-sdk is not installed. Install it with "
            "`pip install eval-hub-sdk` to register this benchmark with a "
            "running EvalHub instance -- `python -m src run` works without it."
        ) from exc

    class _Adapter(FrameworkAdapter):
        def run_benchmark_job(self, config: JobSpec, callbacks: JobCallbacks) -> JobResults:
            start = time.monotonic()
            callbacks.report_status(
                JobStatusUpdate(
                    status=JobStatus.RUNNING,
                    phase=JobPhase.RUNNING_EVALUATION,
                    progress=0.1,
                    message=MessageInfo(message=f"Running {benchmark_id}", message_code="running"),
                )
            )

            local_result = run_local_fn()

            return JobResults(
                id=config.id,
                benchmark_id=config.benchmark_id,
                benchmark_index=config.benchmark_index,
                model_name=config.model.name if config.model else "unknown",
                results=[
                    EvaluationResult(metric_name=name, metric_value=value)
                    for name, value in local_result.metrics.items()
                ],
                overall_score=local_result.score,
                num_examples_evaluated=local_result.num_examples,
                duration_seconds=time.monotonic() - start,
                completed_at=datetime.now(UTC),
                evaluation_metadata={
                    "framework": "eval-suite",
                    "benchmark": benchmark_id,
                    "threshold": local_result.threshold,
                    "passed": local_result.passed,
                    "details": local_result.details[:50],  # cap payload size
                    **local_result.metadata,
                },
            )

    return _Adapter
