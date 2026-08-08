"""Scoring logic shared by the three benchmarks. Kept separate from the
adapters so it can be exercised directly by tests without a live model or
live services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jsonschema

from .dataset import ExtractionCase, FulfillmentScenario

# --- Benchmark 1: extraction accuracy -----------------------------------

_HEADER_FIELDS = ("po_number", "vendor_name", "buyer_name", "ship_to")


@dataclass
class ExtractionScore:
    case_id: str
    header_field_results: dict[str, bool]
    line_item_accuracy: float
    overall_accuracy: float
    mismatches: list[str] = field(default_factory=list)


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def score_extraction(case: ExtractionCase, predicted: dict) -> ExtractionScore:
    header_results: dict[str, bool] = {}
    mismatches: list[str] = []
    for field_name in _HEADER_FIELDS:
        golden_value = case.golden.get(field_name)
        predicted_value = predicted.get(field_name)
        ok = _normalize(golden_value) == _normalize(predicted_value)
        header_results[field_name] = ok
        if not ok:
            mismatches.append(f"{field_name}: expected {golden_value!r}, got {predicted_value!r}")

    golden_items = {item["sku"]: item for item in case.golden["line_items"]}
    predicted_items = {item.get("sku"): item for item in predicted.get("line_items") or []}

    line_item_matches = 0
    for sku, golden_item in golden_items.items():
        predicted_item = predicted_items.get(sku)
        if predicted_item is None:
            mismatches.append(f"line_item {sku}: missing from extraction")
            continue
        matches = (
            _normalize(predicted_item.get("description")) == _normalize(golden_item["description"])
            and float(predicted_item.get("quantity", -1)) == golden_item["quantity"]
            and abs(float(predicted_item.get("unit_price", -1)) - golden_item["unit_price"]) < 0.01
        )
        if matches:
            line_item_matches += 1
        else:
            mismatches.append(
                f"line_item {sku}: expected {golden_item}, got "
                f"{{'description': {predicted_item.get('description')!r}, "
                f"'quantity': {predicted_item.get('quantity')!r}, "
                f"'unit_price': {predicted_item.get('unit_price')!r}}}"
            )

    line_item_accuracy = line_item_matches / len(golden_items) if golden_items else 1.0

    total_checks = len(header_results) + len(golden_items)
    correct_checks = sum(header_results.values()) + line_item_matches
    overall_accuracy = correct_checks / total_checks if total_checks else 1.0

    return ExtractionScore(
        case_id=case.case_id,
        header_field_results=header_results,
        line_item_accuracy=line_item_accuracy,
        overall_accuracy=overall_accuracy,
        mismatches=mismatches,
    )


# --- Benchmark 2: MCP tool-call structure / function / performance -----


@dataclass
class ToolCallScore:
    total_calls: int
    schema_valid_calls: int
    schema_violations: list[str]
    verification_violations: list[str]  # SKUs decremented without prior photo+infer
    latencies_seconds: dict[str, list[float]]  # tool name -> per-call latency

    @property
    def schema_conformance(self) -> float:
        return self.schema_valid_calls / self.total_calls if self.total_calls else 1.0

    @property
    def sequencing_ok(self) -> bool:
        return not self.verification_violations

    @property
    def overall_score(self) -> float:
        """Schema conformance and verification-sequencing are weighted equally --
        a structurally invalid call and a policy-bypassing call are both hard
        failures, so neither should be able to dilute the other into a passing
        aggregate on its own."""
        sequencing_score = 1.0 if self.sequencing_ok else 0.0
        return (self.schema_conformance + sequencing_score) / 2


def _contains_value(obj: Any, needle: str) -> bool:
    if isinstance(obj, str):
        return obj == needle
    if isinstance(obj, dict):
        return any(_contains_value(v, needle) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_value(v, needle) for v in obj)
    return False


def _has_prior_verification(tool_calls: list[dict], before_index: int, sku: str) -> bool:
    photo_idx = None
    for i in range(before_index):
        call = tool_calls[i]
        if call.get("name", "").endswith("get_item_photo") and _contains_value(call.get("arguments"), sku):
            photo_idx = i
    if photo_idx is None:
        return False
    return any(tool_calls[j].get("name", "").endswith("infer_sku") for j in range(photo_idx + 1, before_index))


def score_tool_calls(tool_calls: list[dict], tool_schemas: dict[str, dict]) -> ToolCallScore:
    """`tool_calls` are {"name", "arguments", "ok", "result"} dicts (webhook
    `tool_call` events' `data`, in call order). `tool_schemas` maps prefixed tool
    name (e.g. "wms__adjust_inventory") -> its live MCP inputSchema, fetched
    directly from the running servers so this validates against the real
    contract, not a guess at one."""
    schema_valid = 0
    schema_violations: list[str] = []
    verification_violations: list[str] = []
    latencies: dict[str, list[float]] = {}

    for i, call in enumerate(tool_calls):
        name = call.get("name", "")
        arguments = call.get("arguments") or {}

        schema = tool_schemas.get(name)
        if schema is None:
            schema_violations.append(f"call #{i} ({name}): no such tool is exposed by any connected MCP server")
        else:
            try:
                jsonschema.validate(instance=arguments, schema=schema)
                schema_valid += 1
            except jsonschema.ValidationError as exc:
                schema_violations.append(f"call #{i} ({name}): {exc.message}")

        if name == "wms__adjust_inventory" and float(arguments.get("delta", 0)) < 0:
            sku = arguments.get("sku")
            if sku and not _has_prior_verification(tool_calls, i, sku):
                verification_violations.append(sku)

        latency = call.get("latency_seconds")
        if latency is not None:
            latencies.setdefault(name, []).append(latency)

    return ToolCallScore(
        total_calls=len(tool_calls),
        schema_valid_calls=schema_valid,
        schema_violations=schema_violations,
        verification_violations=verification_violations,
        latencies_seconds=latencies,
    )


# --- Benchmark 3: end-to-end outcome accuracy ---------------------------


@dataclass
class ScenarioScore:
    scenario_id: str
    item_accuracy: float
    order_status_ok: bool
    details: list[str]

    @property
    def passed(self) -> bool:
        return self.item_accuracy == 1.0 and self.order_status_ok


def score_fulfillment_scenario(scenario: FulfillmentScenario, process_order_result: dict) -> ScenarioScore:
    fulfillment = process_order_result.get("fulfillment") or {}
    actual_items = {item.get("sku"): item for item in fulfillment.get("items") or []}

    checks: list[bool] = []
    details: list[str] = []
    for expectation in scenario.line_items:
        actual = actual_items.get(expectation.sku)
        if actual is None:
            checks.append(False)
            details.append(f"{expectation.sku}: missing from fulfillment result")
            continue
        ok = float(actual.get("fulfilled_qty", -1)) == expectation.expected_fulfilled_qty
        checks.append(ok)
        if not ok:
            details.append(
                f"{expectation.sku}: expected fulfilled_qty={expectation.expected_fulfilled_qty}, "
                f"got {actual.get('fulfilled_qty')} (status={actual.get('status')})"
            )

    order_status = fulfillment.get("order_status")
    actual_fully_shipped = order_status == "shipped"
    order_status_ok = actual_fully_shipped == scenario.expect_fully_shipped
    if not order_status_ok:
        details.append(
            f"order_status: expected fully_shipped={scenario.expect_fully_shipped}, "
            f"got order_status={order_status!r}"
        )

    item_accuracy = sum(checks) / len(checks) if checks else 0.0
    return ScenarioScore(
        scenario_id=scenario.scenario_id,
        item_accuracy=item_accuracy,
        order_status_ok=order_status_ok,
        details=details,
    )
