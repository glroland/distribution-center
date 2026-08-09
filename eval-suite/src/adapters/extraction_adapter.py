"""Benchmark 1: prompt evaluation.

Calls the exact same extraction contract local-dc-agent/src/order_extraction.py
uses (same MLflow-registry-or-local prompt id, same forced tool-call schema)
against golden PO PDFs whose fields are known exactly, and scores field-level
extraction accuracy. This is the benchmark that should gate promoting a new
prompt version in the MLflow Prompt Registry before
`PROMPT_SOURCE=mlflow` lets dc-agent pick it up in production -- see
local-dc-agent/src/prompts.py's version tagging.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from ..dataset import build_extraction_dataset
from ..ingest_client import convert_pdf_to_markdown
from ..prompts import load_prompt
from ..scoring import score_extraction
from ..settings import settings
from .base import EvalHubNotInstalled, LocalRunResult, make_framework_adapter

logger = logging.getLogger(__name__)

BENCHMARK_ID = "dc-extraction-accuracy"

_TOOL_NAME = "record_purchase_order"

# Mirrors local-dc-agent/src/order_extraction.py's _TOOL_SCHEMA field-for-field.
# Evaluating a *different* tool contract than production uses would make the
# score meaningless, so this is intentionally kept identical rather than
# simplified -- see prompts.py's module docstring for why this is a hand
# mirror rather than a cross-service import.
_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": "Record the structured fields of a purchase order.",
        "parameters": {
            "type": "object",
            "properties": {
                "po_number": {"type": "string", "description": "The purchase order number/ID."},
                "issue_date": {"type": "string", "description": "The date the PO was issued, as written."},
                "vendor_name": {"type": ["string", "null"]},
                "buyer_name": {"type": "string"},
                "ship_to": {"type": ["string", "null"]},
                "payment_terms": {"type": "string"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": ["string", "null"]},
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_price": {"type": "number"},
                        },
                        "required": ["sku", "description", "quantity", "unit_price"],
                    },
                },
                "stated_subtotal": {"type": "number"},
                "stated_tax": {"type": "number"},
                "stated_total": {"type": "number"},
            },
            "required": ["po_number", "line_items", "vendor_name", "ship_to"],
        },
    },
}


class ExtractedLineItem(BaseModel):
    sku: str | None = None
    description: str
    quantity: float
    unit_price: float


class ExtractedOrder(BaseModel):
    po_number: str
    vendor_name: str | None = None
    buyer_name: str | None = None
    ship_to: str | None = None
    line_items: list[ExtractedLineItem]


class ExtractionError(Exception):
    pass


def _find_tool_input(choices: list[Any]) -> dict[str, Any] | None:
    for choice in choices:
        for tool_call in getattr(choice.message, "tool_calls", None) or []:
            if tool_call.function.name == _TOOL_NAME:
                try:
                    return json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    return None
    return None


def extract_order(markdown: str) -> ExtractedOrder:
    if not settings.OPENAI_API_KEY:
        raise ExtractionError("OPENAI_API_KEY is not configured")

    client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL or None,
        timeout=settings.OPENAI_REQUEST_TIMEOUT_SECONDS,
    )
    system_prompt = load_prompt("dc-agent.order_extraction.system_prompt").format()
    try:
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            max_completion_tokens=2048,
            temperature=0,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": markdown},
            ],
        )
    except Exception as exc:
        raise ExtractionError(f"OpenAI API call failed: {exc}") from exc

    tool_input = _find_tool_input(response.choices)
    if tool_input is None:
        raise ExtractionError("Model did not return a record_purchase_order tool call")

    try:
        return ExtractedOrder.model_validate(tool_input)
    except ValidationError as exc:
        raise ExtractionError(f"Model output failed schema validation: {exc}") from exc


async def run_local(n: int = 5, seed: int = 42, output_dir: Path | None = None) -> LocalRunResult:
    with tempfile.TemporaryDirectory() as tmp:
        cases_dir = output_dir or Path(tmp)
        cases = build_extraction_dataset(n=n, seed=seed, output_dir=cases_dir)

        scores = []
        details: list[str] = []
        for case in cases:
            try:
                markdown = await convert_pdf_to_markdown(case.pdf_path.read_bytes(), case.pdf_path.name)
                predicted = extract_order(markdown)
            except Exception as exc:  # noqa: BLE001 - a failed case scores zero, doesn't abort the run
                details.append(f"{case.case_id}: extraction failed: {exc}")
                scores.append(0.0)
                continue

            score = score_extraction(case, predicted.model_dump())
            scores.append(score.overall_accuracy)
            if score.mismatches:
                details.append(f"{case.case_id}: " + "; ".join(score.mismatches))

    mean_accuracy = sum(scores) / len(scores) if scores else 0.0
    return LocalRunResult(
        benchmark_id=BENCHMARK_ID,
        score=mean_accuracy,
        threshold=settings.EXTRACTION_FIELD_ACCURACY_THRESHOLD,
        metrics={"mean_field_accuracy": mean_accuracy, "num_cases": float(len(cases))},
        num_examples=len(cases),
        details=details,
    )


def _run_local_sync() -> LocalRunResult:
    import asyncio

    return asyncio.run(run_local())


ExtractionFrameworkAdapter = None
try:
    ExtractionFrameworkAdapter = make_framework_adapter(BENCHMARK_ID, _run_local_sync)
except EvalHubNotInstalled:  # fine for local-only use -- see base.py
    pass
