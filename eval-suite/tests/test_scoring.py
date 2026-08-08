from pathlib import Path

from src.dataset import ExtractionCase, FulfillmentScenario, ScenarioLineExpectation
from src.scoring import score_extraction, score_fulfillment_scenario, score_tool_calls

_GOLDEN = {
    "po_number": "PO1",
    "vendor_name": "Acme",
    "buyer_name": "Buyer",
    "ship_to": "1 Main St",
    "line_items": [{"sku": "SKU-1", "description": "Widget", "quantity": 5.0, "unit_price": 10.0}],
}


def _case() -> ExtractionCase:
    return ExtractionCase(case_id="X1", pdf_path=Path("/dev/null"), golden=_GOLDEN)


def test_score_extraction_perfect_match():
    score = score_extraction(_case(), dict(_GOLDEN))
    assert score.overall_accuracy == 1.0
    assert not score.mismatches


def test_score_extraction_penalizes_wrong_field_and_wrong_line_item():
    predicted = {
        "po_number": "PO1",
        "vendor_name": "Wrong Co",
        "buyer_name": "Buyer",
        "ship_to": "1 Main St",
        "line_items": [{"sku": "SKU-1", "description": "Widget", "quantity": 4.0, "unit_price": 10.0}],
    }
    score = score_extraction(_case(), predicted)
    # 3/4 header fields correct + 0/1 line items correct = 3 of 5 checks
    assert score.overall_accuracy == 3 / 5
    assert len(score.mismatches) == 2


_SCHEMAS = {
    "wms__adjust_inventory": {
        "type": "object",
        "properties": {"sku": {"type": "string"}, "delta": {"type": "number"}},
        "required": ["sku", "delta"],
    },
    "robot__get_item_photo": {
        "type": "object",
        "properties": {"sku": {"type": "string"}},
        "required": ["sku"],
    },
    "label__infer_sku": {
        "type": "object",
        "properties": {"image_base64": {"type": "string"}},
        "required": ["image_base64"],
    },
}


def test_score_tool_calls_passes_when_verification_precedes_decrement():
    calls = [
        {"name": "robot__get_item_photo", "arguments": {"sku": "SKU-1"}},
        {"name": "label__infer_sku", "arguments": {"image_base64": "abc"}},
        {"name": "wms__adjust_inventory", "arguments": {"sku": "SKU-1", "delta": -2}},
    ]
    score = score_tool_calls(calls, _SCHEMAS)
    assert score.overall_score == 1.0
    assert score.sequencing_ok
    assert not score.schema_violations


def test_score_tool_calls_flags_decrement_without_verification():
    calls = [{"name": "wms__adjust_inventory", "arguments": {"sku": "SKU-1", "delta": -2}}]
    score = score_tool_calls(calls, _SCHEMAS)
    assert score.verification_violations == ["SKU-1"]
    assert not score.sequencing_ok


def test_score_tool_calls_flags_schema_violation():
    calls = [{"name": "wms__adjust_inventory", "arguments": {"sku": "SKU-1"}}]  # missing "delta"
    score = score_tool_calls(calls, _SCHEMAS)
    assert score.schema_valid_calls == 0
    assert score.schema_violations


def test_score_tool_calls_flags_unknown_tool():
    calls = [{"name": "wms__made_up_tool", "arguments": {}}]
    score = score_tool_calls(calls, _SCHEMAS)
    assert score.schema_conformance == 0.0


def _scenario(expect_fully_shipped: bool = True) -> FulfillmentScenario:
    return FulfillmentScenario(
        scenario_id="S1",
        pdf_path=Path("/dev/null"),
        po_number="PO1",
        line_items=[ScenarioLineExpectation(sku="SKU-1", requested_qty=5, expected_fulfilled_qty=5)],
        expect_fully_shipped=expect_fully_shipped,
    )


def test_score_fulfillment_scenario_passes_on_exact_match():
    result = {"fulfillment": {"order_status": "shipped", "items": [{"sku": "SKU-1", "fulfilled_qty": 5}]}}
    score = score_fulfillment_scenario(_scenario(), result)
    assert score.passed


def test_score_fulfillment_scenario_fails_on_short_fulfillment():
    result = {
        "fulfillment": {"order_status": "partially_shipped", "items": [{"sku": "SKU-1", "fulfilled_qty": 3}]}
    }
    score = score_fulfillment_scenario(_scenario(), result)
    assert not score.passed
    assert score.item_accuracy == 0.0
    assert not score.order_status_ok


def test_score_fulfillment_scenario_missing_line_item_fails():
    result = {"fulfillment": {"order_status": "shipped", "items": []}}
    score = score_fulfillment_scenario(_scenario(), result)
    assert not score.passed
    assert "missing from fulfillment result" in score.details[0]
