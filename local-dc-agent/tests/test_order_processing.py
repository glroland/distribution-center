from src.models import Escalation, ExtractedOrder, FulfillmentResult, LineItem, Shipment
from src.order_processing import process_order, summarize


def _order(**overrides) -> ExtractedOrder:
    defaults = dict(
        po_number="PO-1001",
        vendor_name="Acme Industrial Supply",
        line_items=[
            LineItem(sku="SKU-1", description="Widget", quantity=2, unit_price=10.0),
            LineItem(sku="SKU-2", description="Gadget", quantity=1, unit_price=5.0),
        ],
    )
    defaults.update(overrides)
    return ExtractedOrder(**defaults)


def test_computed_subtotal_and_no_mismatch_when_totals_agree() -> None:
    result = process_order(_order(stated_total=25.0))

    assert result.computed_subtotal == 25.0
    assert result.totals_mismatch is False
    assert result.dc_order_id.startswith("dc-PO-1001-")


def test_mismatch_flagged_when_stated_total_disagrees() -> None:
    result = process_order(_order(stated_total=999.0))

    assert result.totals_mismatch is True


def test_no_mismatch_when_no_stated_total_present() -> None:
    result = process_order(_order())

    assert result.totals_mismatch is False


def test_tax_is_accounted_for_in_mismatch_check() -> None:
    result = process_order(_order(stated_tax=2.5, stated_total=27.5))

    assert result.totals_mismatch is False


def test_summarize_includes_warning_on_mismatch() -> None:
    result = process_order(_order(stated_total=999.0))

    text = summarize(result)

    assert "PO-1001" in text
    assert "WARNING" in text


def test_summarize_includes_tracking_number_when_shipped() -> None:
    result = process_order(_order(stated_total=25.0))
    result.fulfillment = FulfillmentResult(
        items=[],
        shipment=Shipment(carrier="UPS", tracking_number="1Z999", estimated_delivery="2026-08-05"),
        order_status="shipped",
        summary="Shipped everything.",
    )

    text = summarize(result)

    assert "UPS" in text
    assert "1Z999" in text


def test_summarize_includes_escalation_when_present() -> None:
    result = process_order(_order(stated_total=25.0))
    result.fulfillment = FulfillmentResult(
        items=[],
        escalations=[Escalation(sku="SKU-9999", question="Unknown SKU")],
        order_status="escalated",
        summary="Escalated unknown SKU.",
    )

    text = summarize(result)

    assert "ESCALATED" in text
    assert "SKU-9999" in text
