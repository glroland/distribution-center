"""Manual mlflow.chat.tokenUsage / mlflow.llm.cost span attributes.

mlflow.openai.autolog() already attaches real token counts to a span for
every OpenAI call, but its built-in cost calculator only recognizes public
models with a known published per-token price (via LiteLLM's/MLflow's
pricing catalog) -- a self-hosted OPENAI_MODEL like gemma-4 always comes
back with tokens but no cost. This computes cost from
settings.LLM_COST_PER_MILLION_TOKENS instead, and records both token usage
and cost on the *current active span* -- in this codebase that's always the
mlflow.trace-decorated function the caller lives in (extract_order,
fulfill_order), which is already an ancestor of autolog's own per-call
child span. MLflow's own trace-level aggregation skips a descendant span's
value once an ancestor already carries the same attribute, so recording
totals here doesn't double-count against autolog's per-call numbers.

A no-op whenever there's no active span (e.g. tracing disabled, as in
tests) or no usage to record.
"""

import mlflow

from .settings import settings


def usage_totals(*usages) -> dict[str, int]:
    """Sum one or more OpenAI response `usage` objects into a
    mlflow.chat.tokenUsage-shaped dict. Ignores any that are None."""
    input_tokens = sum(u.prompt_tokens for u in usages if u is not None)
    output_tokens = sum(u.completion_tokens for u in usages if u is not None)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def cost_from_totals(totals: dict[str, int], cost_per_million_tokens: float) -> dict[str, float]:
    """Compute a mlflow.llm.cost-shaped dict from token totals and a flat $/million-token rate."""
    input_cost = totals["input_tokens"] / 1_000_000 * cost_per_million_tokens
    output_cost = totals["output_tokens"] / 1_000_000 * cost_per_million_tokens
    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost,
    }


def record_usage_and_cost(*usages) -> None:
    """Set cumulative token usage and cost on the current active span, computed
    from `usages` (one or more OpenAI response `usage` objects) and
    settings.LLM_COST_PER_MILLION_TOKENS. No-op if there's no active span or
    every usage is None."""
    span = mlflow.get_current_active_span()
    if span is None:
        return

    totals = usage_totals(*usages)
    if totals["total_tokens"] == 0:
        return

    span.set_attribute("mlflow.chat.tokenUsage", totals)
    span.set_attribute("mlflow.llm.cost", cost_from_totals(totals, settings.LLM_COST_PER_MILLION_TOKENS))
