import pytest

from src.shipping import (
    InvalidShipmentError,
    ShipmentNotFoundError,
    ShippingStore,
    TrackingNumberNotFoundError,
)

_KNOWN_CARRIERS = {"UPS", "FedEx", "USPS", "DHL"}


def _store() -> ShippingStore:
    return ShippingStore()


def test_create_shipment() -> None:
    store = _store()
    shipment = store.create_shipment(
        "PO-1001", "Jane Doe", "123 Main St, Springfield", [("SKU-1001", 10), ("SKU-1002", 5)]
    )
    assert shipment.id == 1
    assert shipment.po_number == "PO-1001"
    assert shipment.customer_name == "Jane Doe"
    assert shipment.customer_address == "123 Main St, Springfield"
    assert [(i.sku, i.qty) for i in shipment.items] == [("SKU-1001", 10), ("SKU-1002", 5)]
    assert shipment.status == "shipped"
    assert shipment.carrier in _KNOWN_CARRIERS
    assert shipment.estimated_delivery > shipment.shipped_at


def test_create_shipment_assigns_incrementing_ids() -> None:
    store = _store()
    first = store.create_shipment("PO-1", "A", "addr", [("SKU-1", 1)])
    second = store.create_shipment("PO-2", "B", "addr", [("SKU-1", 1)])
    assert (first.id, second.id) == (1, 2)


def test_create_shipment_generates_unique_tracking_numbers() -> None:
    store = _store()
    first = store.create_shipment("PO-1", "A", "addr", [("SKU-1", 1)])
    second = store.create_shipment("PO-2", "B", "addr", [("SKU-1", 1)])
    assert first.tracking_number != second.tracking_number


def test_tracking_number_format_matches_carrier() -> None:
    store = _store()
    for _ in range(20):
        shipment = store.create_shipment("PO-1", "A", "addr", [("SKU-1", 1)])
        if shipment.carrier == "UPS":
            assert shipment.tracking_number.startswith("1Z")
            assert len(shipment.tracking_number) == 18
        elif shipment.carrier == "FedEx":
            assert shipment.tracking_number.isdigit()
            assert len(shipment.tracking_number) == 12
        elif shipment.carrier == "USPS":
            assert shipment.tracking_number.startswith("9400")
            assert len(shipment.tracking_number) == 22
        elif shipment.carrier == "DHL":
            assert shipment.tracking_number.isdigit()
            assert len(shipment.tracking_number) == 10


def test_create_shipment_rejects_blank_po_number() -> None:
    store = _store()
    with pytest.raises(InvalidShipmentError):
        store.create_shipment("  ", "A", "addr", [("SKU-1", 1)])


def test_create_shipment_rejects_blank_customer_name() -> None:
    store = _store()
    with pytest.raises(InvalidShipmentError):
        store.create_shipment("PO-1", "  ", "addr", [("SKU-1", 1)])


def test_create_shipment_rejects_blank_customer_address() -> None:
    store = _store()
    with pytest.raises(InvalidShipmentError):
        store.create_shipment("PO-1", "A", "  ", [("SKU-1", 1)])


def test_create_shipment_rejects_empty_items() -> None:
    store = _store()
    with pytest.raises(InvalidShipmentError):
        store.create_shipment("PO-1", "A", "addr", [])


def test_create_shipment_rejects_non_positive_qty() -> None:
    store = _store()
    with pytest.raises(InvalidShipmentError):
        store.create_shipment("PO-1", "A", "addr", [("SKU-1", 0)])


def test_list_shipments_returns_all() -> None:
    store = _store()
    store.create_shipment("PO-1", "A", "addr", [("SKU-1", 1)])
    store.create_shipment("PO-2", "B", "addr", [("SKU-1", 1)])
    assert len(store.list_shipments()) == 2


def test_list_shipments_filters_by_po_number() -> None:
    store = _store()
    first = store.create_shipment("PO-1", "A", "addr", [("SKU-1", 1)])
    store.create_shipment("PO-2", "B", "addr", [("SKU-1", 1)])
    matches = store.list_shipments(po_number="PO-1")
    assert [s.id for s in matches] == [first.id]


def test_get_shipment() -> None:
    store = _store()
    created = store.create_shipment("PO-1", "A", "addr", [("SKU-1", 1)])
    assert store.get_shipment(created.id) is created


def test_get_shipment_unknown_id_raises() -> None:
    store = _store()
    with pytest.raises(ShipmentNotFoundError):
        store.get_shipment(999)


def test_get_shipment_by_tracking() -> None:
    store = _store()
    created = store.create_shipment("PO-1", "A", "addr", [("SKU-1", 1)])
    assert store.get_shipment_by_tracking(created.tracking_number) is created


def test_get_shipment_by_tracking_unknown_raises() -> None:
    store = _store()
    with pytest.raises(TrackingNumberNotFoundError):
        store.get_shipment_by_tracking("does-not-exist")


def test_reset_clears_shipments_and_id_counter() -> None:
    store = _store()
    store.create_shipment("PO-1", "A", "addr", [("SKU-1", 1)])
    store.reset()
    assert store.list_shipments() == []
    fresh = store.create_shipment("PO-2", "B", "addr", [("SKU-1", 1)])
    assert fresh.id == 1
