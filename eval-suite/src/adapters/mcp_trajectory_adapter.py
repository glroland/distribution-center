"""Benchmark 2: agent-vs-MCP-server evaluation.

Runs real purchase orders through local-dc-agent over A2A (exactly as
dashboard-ui does), captures the exact stream of MCP tool calls the
fulfillment loop made via the same `progress_webhook` mechanism
dashboard-ui's live view uses, and scores it against the *live* MCP tool
schemas (fetched directly from the five running servers, not hardcoded) for:

  - structure: does every call's arguments validate against its tool's real
    inputSchema?
  - function: for every SKU whose stock got decremented, did a
    robot__get_item_photo -> label__infer_sku pair happen first, per the
    fulfillment policy prompt's visual-verification requirement (see
    CLAUDE.md's "The dc-agent pipeline" section)?
  - performance: wall-clock time between consecutive tool-call events. This
    is an approximation, not isolated per-tool latency: the webhook only
    fires after each call completes, so the gap also includes the model's
    think-time for the next turn -- reported as
    `mean_inter_call_latency_seconds`, not "tool latency", to keep that
    honest.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..agent_client import AgentCallError, process_purchase_order
from ..dataset import build_fulfillment_scenarios
from ..mcp_schema_client import fetch_live_tool_schemas
from ..scoring import score_tool_calls
from ..settings import settings
from ..webhook_receiver import WebhookReceiver
from .base import EvalHubNotInstalled, LocalRunResult, make_framework_adapter

BENCHMARK_ID = "dc-mcp-trajectory"


async def run_local() -> LocalRunResult:
    tool_schemas = await fetch_live_tool_schemas()

    with tempfile.TemporaryDirectory() as tmp:
        scenarios = build_fulfillment_scenarios(Path(tmp))

        all_tool_calls: list[dict] = []
        details: list[str] = []
        per_scenario_scores = []

        for scenario in scenarios:
            with WebhookReceiver(host=settings.WEBHOOK_HOST, port=settings.WEBHOOK_PORT) as receiver:
                try:
                    await process_purchase_order(
                        settings.AGENT_URL,
                        scenario.pdf_path,
                        progress_webhook=receiver.url,
                        timeout=settings.AGENT_CALL_TIMEOUT_SECONDS,
                    )
                except AgentCallError as exc:
                    details.append(f"{scenario.scenario_id}: agent call failed: {exc}")
                    continue

                events = receiver.events_of_type("tool_call")

            tool_calls: list[dict] = []
            prev_ts: float | None = None
            for event in events:
                data = dict(event.get("data") or {})
                ts = event["received_at"]
                if prev_ts is not None:
                    data["latency_seconds"] = ts - prev_ts
                prev_ts = ts
                tool_calls.append(data)

            score = score_tool_calls(tool_calls, tool_schemas)
            per_scenario_scores.append(score)
            all_tool_calls.extend(tool_calls)
            if score.schema_violations:
                details.append(f"{scenario.scenario_id}: schema violations: {score.schema_violations}")
            if score.verification_violations:
                details.append(
                    f"{scenario.scenario_id}: SKU(s) decremented without a prior "
                    f"get_item_photo -> infer_sku pair: {score.verification_violations}"
                )

    if not per_scenario_scores:
        return LocalRunResult(
            benchmark_id=BENCHMARK_ID,
            score=0.0,
            threshold=settings.MCP_TRAJECTORY_SCORE_THRESHOLD,
            metrics={},
            num_examples=0,
            details=details or ["no scenario produced any MCP tool calls"],
        )

    overall_score = sum(s.overall_score for s in per_scenario_scores) / len(per_scenario_scores)
    schema_conformance = sum(s.schema_conformance for s in per_scenario_scores) / len(per_scenario_scores)
    latencies = [c["latency_seconds"] for c in all_tool_calls if c.get("latency_seconds") is not None]
    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0

    return LocalRunResult(
        benchmark_id=BENCHMARK_ID,
        score=overall_score,
        threshold=settings.MCP_TRAJECTORY_SCORE_THRESHOLD,
        metrics={
            "schema_conformance": schema_conformance,
            "mean_inter_call_latency_seconds": mean_latency,
            "total_tool_calls": float(len(all_tool_calls)),
        },
        num_examples=len(per_scenario_scores),
        details=details,
    )


def _run_local_sync() -> LocalRunResult:
    import asyncio

    return asyncio.run(run_local())


McpTrajectoryFrameworkAdapter = None
try:
    McpTrajectoryFrameworkAdapter = make_framework_adapter(BENCHMARK_ID, _run_local_sync)
except EvalHubNotInstalled:
    pass
