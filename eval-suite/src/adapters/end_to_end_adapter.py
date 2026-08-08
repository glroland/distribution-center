"""Benchmark 3: agent-vs-agent (end-to-end) evaluation.

Submits purchase orders with a stock-availability outcome that's known in
advance (see seed_data.py / dataset.py) to local-dc-agent over A2A -- the
same call dashboard-ui makes -- and checks whether the *final* result
matches: the right quantity fulfilled per line item, and the right coarse
order outcome (fully shipped vs. not). Deliberately rules-based rather than
an LLM-as-judge: correctness here is a fact computable from the seed CSVs,
not a matter of taste, so a judge model would only add noise and cost.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..agent_client import AgentCallError, process_purchase_order
from ..dataset import build_fulfillment_scenarios
from ..scoring import score_fulfillment_scenario
from ..settings import settings
from .base import EvalHubNotInstalled, LocalRunResult, make_framework_adapter

BENCHMARK_ID = "dc-end-to-end"


async def run_local() -> LocalRunResult:
    with tempfile.TemporaryDirectory() as tmp:
        scenarios = build_fulfillment_scenarios(Path(tmp))

        scenario_scores: list[float] = []
        details: list[str] = []
        for scenario in scenarios:
            try:
                response = await process_purchase_order(
                    settings.AGENT_URL,
                    scenario.pdf_path,
                    timeout=settings.AGENT_CALL_TIMEOUT_SECONDS,
                )
            except AgentCallError as exc:
                details.append(f"{scenario.scenario_id}: agent call failed: {exc}")
                scenario_scores.append(0.0)
                continue

            score = score_fulfillment_scenario(scenario, response["result"])
            scenario_scores.append(1.0 if score.passed else score.item_accuracy)
            if not score.passed:
                details.append(f"{scenario.scenario_id}: " + "; ".join(score.details))

    mean_score = sum(scenario_scores) / len(scenario_scores) if scenario_scores else 0.0
    return LocalRunResult(
        benchmark_id=BENCHMARK_ID,
        score=mean_score,
        threshold=settings.END_TO_END_SUCCESS_THRESHOLD,
        metrics={"mean_scenario_score": mean_score, "num_scenarios": float(len(scenarios))},
        num_examples=len(scenarios),
        details=details,
    )


def _run_local_sync() -> LocalRunResult:
    import asyncio

    return asyncio.run(run_local())


EndToEndFrameworkAdapter = None
try:
    EndToEndFrameworkAdapter = make_framework_adapter(BENCHMARK_ID, _run_local_sync)
except EvalHubNotInstalled:
    pass
